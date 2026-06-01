import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation import BroadcastCampaign
from app.shared.tenant import normalize_tenant_id


def create_broadcast_campaign(db: Session, tenant_id: str, payload: dict) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    template = str(payload.get("template") or payload.get("template_name") or "").strip()
    audience = payload.get("audience") or payload.get("phones") or []
    if not template:
        raise ValueError("template is required")
    if not isinstance(audience, list) or not audience:
        raise ValueError("audience must be a non-empty list")
    phones = [str(phone).strip() for phone in audience if str(phone).strip()]
    if not phones:
        raise ValueError("audience must contain at least one phone")

    row = BroadcastCampaign(
        tenant_id=tenant_id,
        name=str(payload.get("name") or template).strip()[:200],
        template=template,
        audience=json.dumps(phones, ensure_ascii=True),
        variables=json.dumps(payload.get("variables") or {}, ensure_ascii=True, default=str),
        status="queued",
        sent_count=0,
        failed_count=0,
        created_by=payload.get("created_by"),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_broadcast_campaign(row)


def list_broadcast_campaigns(db: Session, tenant_id: str, limit: int = 50) -> list[dict]:
    rows = db.execute(
        select(BroadcastCampaign)
        .where(BroadcastCampaign.tenant_id == normalize_tenant_id(tenant_id))
        .order_by(BroadcastCampaign.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).scalars().all()
    return [serialize_broadcast_campaign(row) for row in rows]


def serialize_broadcast_campaign(row: BroadcastCampaign) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "template": row.template,
        "audience": _loads(row.audience, []),
        "variables": _loads(row.variables, {}),
        "status": row.status,
        "sent_count": row.sent_count or 0,
        "failed_count": row.failed_count or 0,
        "created_at": str(row.created_at) if row.created_at else None,
    }


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
