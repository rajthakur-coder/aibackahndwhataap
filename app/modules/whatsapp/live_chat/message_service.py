from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contact import Contact, ContactTag, Tag
from app.models.whatsapp import Message
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential, whatsapp_access_token
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


from app.modules.whatsapp.live_chat.contact_service import *

def get_chat_messages(db: Session, contact: str) -> dict:
    tenant_id = _live_chat_tenant_id()
    rows = db.execute(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.phone == contact)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).scalars().all()

    for row in rows:
        if row.direction == "incoming" and row.status != "read":
            row.status = "read"
    db.commit()

    return {
        "success": True,
        "statusCode": 1,
        "message": "messages fetched",
        "data": [serialize_message(row) for row in rows],
    }

def send_live_chat_text(
    db: Session,
    *,
    to_no: str,
    message_body: str,
) -> dict:
    tenant_id = _live_chat_tenant_id()
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if not credential or not token or not credential.phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    latest_incoming = db.execute(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.phone == to_no, Message.direction == "incoming")
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).scalars().first()
    if not _is_24_hour_window_open(latest_incoming.created_at if latest_incoming else None):
        raise RuntimeError("24h window closed. Send template message only.")

    payload = {
        "messaging_product": "whatsapp",
        "to": to_no,
        "type": "text",
        "text": {"body": message_body[:4096]},
    }
    response = requests.post(
        f"{settings.WHATSAPP_BASE_URL}/{credential.phone_number_id}/messages",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    whatsapp_message_id = (body.get("messages") or [{}])[0].get("id")

    row = Message(
        tenant_id=tenant_id,
        phone=to_no,
        message=message_body,
        direction="outgoing",
        status="sent",
        message_type="text",
        whatsapp_message_id=whatsapp_message_id,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "success": True,
        "statusCode": 1,
        "message": "Message sent successfully",
        "data": serialize_message(row),
    }

def mark_chat_read(db: Session, message_id: str | None = None, contact: str | None = None) -> dict:
    tenant_id = _live_chat_tenant_id()
    statement = select(Message).where(Message.tenant_id == tenant_id, Message.direction == "incoming")
    if contact:
        statement = statement.where(Message.phone == contact)
    rows = db.execute(statement).scalars().all()
    for row in rows:
        if message_id and str(row.whatsapp_message_id or row.id) != str(message_id):
            continue
        row.status = "read"
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Message status updated"}

def _live_chat_tenant_id() -> str:
    return normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)

__all__ = [
    "get_chat_messages",
    "send_live_chat_text",
    "mark_chat_read",
]
