import json
from datetime import datetime
from urllib.parse import urlencode

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import WhatsappInteractionEvent
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


def analytics_base_url() -> str:
    return (settings.PUBLIC_WEBHOOK_BASE_URL or settings.APP_URL or "").rstrip("/")


def tracking_url(target_url: str | None, phone: str | None = None, source: str | None = None, title: str | None = None) -> str | None:
    if not target_url:
        return target_url
    base_url = analytics_base_url()
    if not base_url:
        return target_url
    tracking_prefix = f"{base_url}/whatsapp/analytics/track-click"
    if target_url.startswith(tracking_prefix):
        return target_url
    params = {"target": target_url}
    if phone:
        params["phone"] = phone
    if source:
        params["source"] = source
    if title:
        params["title"] = title
    tenant_id = normalize_tenant_id(current_tenant_id())
    if tenant_id != DEFAULT_TENANT_ID:
        params["tenant_id"] = tenant_id
    return f"{base_url}/whatsapp/analytics/track-click?{urlencode(params)}"


def log_interactive_click(db: Session, phone: str, message_id: str | None, message_payload: dict, tenant_id: str | None = None) -> WhatsappInteractionEvent | None:
    tenant_id = normalize_tenant_id(tenant_id or current_tenant_id() or DEFAULT_TENANT_ID)
    payload = message_payload.get("message") if isinstance(message_payload.get("message"), dict) else message_payload
    interactive = payload.get("interactive") or {}
    interaction_type = interactive.get("type")
    if interaction_type == "button_reply":
        reply = interactive.get("button_reply") or {}
        event_type = "button_click"
        source = "whatsapp_button"
    elif interaction_type == "list_reply":
        reply = interactive.get("list_reply") or {}
        event_type = "list_click"
        source = "whatsapp_list"
    else:
        return None

    event = WhatsappInteractionEvent(
        tenant_id=tenant_id,
        phone=phone,
        event_type=event_type,
        source=source,
        message_id=message_id,
        interaction_id=str(reply.get("id") or ""),
        title=str(reply.get("title") or ""),
        payload=json.dumps(payload, ensure_ascii=True),
    )
    db.add(event)
    return event


def log_link_click(
    db: Session,
    target_url: str,
    phone: str | None = None,
    source: str | None = None,
    title: str | None = None,
    tenant_id: str | None = None,
) -> WhatsappInteractionEvent:
    tenant_id = normalize_tenant_id(tenant_id or current_tenant_id() or DEFAULT_TENANT_ID)
    event = WhatsappInteractionEvent(
        tenant_id=tenant_id,
        phone=phone,
        event_type="link_click",
        source=source or "tracked_url",
        title=title,
        target_url=target_url,
        payload=json.dumps(
            {
                "target_url": target_url,
                "phone": phone,
                "source": source,
                "title": title,
            },
            ensure_ascii=True,
        ),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def analytics_summary(db: Session, since: datetime | None = None) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    base = select(WhatsappInteractionEvent).where(WhatsappInteractionEvent.tenant_id == tenant_id)
    if since:
        base = base.where(WhatsappInteractionEvent.created_at >= since)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    def grouped(*columns):
        statement = select(*columns, func.count().label("count")).select_from(WhatsappInteractionEvent).where(WhatsappInteractionEvent.tenant_id == tenant_id)
        if since:
            statement = statement.where(WhatsappInteractionEvent.created_at >= since)
        statement = statement.group_by(*columns).order_by(desc("count")).limit(50)
        return [dict(row._mapping) for row in db.execute(statement).all()]

    return {
        "total": total,
        "by_event_type": grouped(WhatsappInteractionEvent.event_type),
        "by_button_or_link": grouped(
            WhatsappInteractionEvent.event_type,
            WhatsappInteractionEvent.interaction_id,
            WhatsappInteractionEvent.title,
            WhatsappInteractionEvent.target_url,
        ),
        "by_phone": grouped(WhatsappInteractionEvent.phone),
    }


def list_analytics_events(db: Session, limit: int = 100, offset: int = 0, event_type: str | None = None) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)
    statement = select(WhatsappInteractionEvent).where(WhatsappInteractionEvent.tenant_id == tenant_id)
    count_statement = select(func.count()).select_from(WhatsappInteractionEvent).where(WhatsappInteractionEvent.tenant_id == tenant_id)
    if event_type:
        statement = statement.where(WhatsappInteractionEvent.event_type == event_type)
        count_statement = count_statement.where(WhatsappInteractionEvent.event_type == event_type)

    rows = db.execute(
        statement.order_by(WhatsappInteractionEvent.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "phone": row.phone,
                "event_type": row.event_type,
                "source": row.source,
                "message_id": row.message_id,
                "interaction_id": row.interaction_id,
                "title": row.title,
                "target_url": row.target_url,
                "created_at": str(row.created_at),
            }
            for row in rows
        ],
        "total": db.scalar(count_statement) or 0,
        "limit": limit,
        "offset": offset,
    }
