import json

from sqlalchemy.orm import Session

from app.models.whatsapp import Message
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


def save_message(
    db: Session,
    phone: str,
    message: str,
    direction: str,
    whatsapp_message_id: str | None = None,
    message_type: str = "text",
    payload: dict | list | str | None = None,
    tenant_id: str | None = None,
) -> Message:
    if isinstance(payload, (dict, list)):
        payload_value = json.dumps(payload, ensure_ascii=True)
    else:
        payload_value = payload

    row = Message(
        tenant_id=normalize_tenant_id(tenant_id or current_tenant_id() or DEFAULT_TENANT_ID),
        phone=phone,
        message=message,
        direction=direction,
        status="received" if direction == "incoming" else "sent",
        message_type=message_type,
        payload=payload_value,
        whatsapp_message_id=whatsapp_message_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    from app.modules.whatsapp.live_chat.contact_service import update_contact_from_message

    update_contact_from_message(
        db,
        phone=phone,
        message=message,
        direction=direction,
        message_type=message_type,
        created_at=row.created_at,
        tenant_id=row.tenant_id,
    )
    return row
