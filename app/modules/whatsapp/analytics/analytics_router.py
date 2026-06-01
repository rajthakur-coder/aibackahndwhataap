from datetime import datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.whatsapp import Message, WebhookEvent
from app.security import get_current_user_token
from app.modules.whatsapp.analytics.analytics_service import (
    analytics_summary,
    list_analytics_events,
    log_link_click,
)
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id, reset_current_tenant_id, set_current_tenant_id


whatsapp_analytics_router = APIRouter(tags=["whatsapp"])


@whatsapp_analytics_router.get("/whatsapp/analytics/track-click")
async def track_whatsapp_link_click(
    target: str,
    phone: str | None = None,
    source: str | None = None,
    title: str | None = None,
    tenant_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid target URL")

    resolved_tenant_id = normalize_tenant_id(tenant_id)
    if resolved_tenant_id == DEFAULT_TENANT_ID and phone:
        resolved_tenant_id = await db.run_sync(
            lambda sync_db: _resolve_tracking_tenant_id(sync_db, phone)
        )
    if resolved_tenant_id == DEFAULT_TENANT_ID:
        return RedirectResponse(target, status_code=302)

    tenant_token = set_current_tenant_id(resolved_tenant_id)
    try:
        await db.run_sync(
            lambda sync_db: log_link_click(
                sync_db,
                target_url=target,
                phone=phone,
                source=source,
                title=title,
                tenant_id=resolved_tenant_id,
            )
        )
    except Exception:
        await db.rollback()
    finally:
        reset_current_tenant_id(tenant_token)
    return RedirectResponse(target, status_code=302)


def _resolve_tracking_tenant_id(sync_db, phone: str | None) -> str:
    phone = str(phone or "").strip()
    if not phone:
        return DEFAULT_TENANT_ID

    message_tenant_id = sync_db.execute(
        select(Message.tenant_id)
        .where(Message.phone == phone, Message.tenant_id != DEFAULT_TENANT_ID)
        .order_by(Message.updated_at.desc())
        .limit(1)
    ).scalar()
    tenant_id = normalize_tenant_id(message_tenant_id)
    if tenant_id != DEFAULT_TENANT_ID:
        return tenant_id

    event_tenant_id = sync_db.execute(
        select(WebhookEvent.tenant_id)
        .where(WebhookEvent.phone == phone, WebhookEvent.tenant_id != DEFAULT_TENANT_ID)
        .order_by(WebhookEvent.updated_at.desc())
        .limit(1)
    ).scalar()
    tenant_id = normalize_tenant_id(event_tenant_id)
    return tenant_id if tenant_id != DEFAULT_TENANT_ID else DEFAULT_TENANT_ID


@whatsapp_analytics_router.get("/whatsapp/analytics/summary")
async def get_whatsapp_analytics_summary(
    days: int | None = None,
    _current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    since = None
    if days:
        since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    return await db.run_sync(lambda sync_db: analytics_summary(sync_db, since=since))


@whatsapp_analytics_router.get("/whatsapp/analytics/events")
async def get_whatsapp_analytics_events(
    limit: int = 100,
    offset: int = 0,
    event_type: str | None = None,
    _current_user=Depends(get_current_user_token),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: list_analytics_events(
            sync_db,
            limit=limit,
            offset=offset,
            event_type=event_type,
        )
    )
