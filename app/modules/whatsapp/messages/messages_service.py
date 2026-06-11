import json
import logging

from redis import Redis
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import Message
from app.modules.whatsapp.live_chat.socket import LIVE_CHAT_EVENTS_CHANNEL
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


log = logging.getLogger(__name__)


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
    if direction == "outgoing":
        _publish_saved_live_chat_message(row)
    return row


def _publish_saved_live_chat_message(row: Message) -> None:
    try:
        from app.modules.whatsapp.live_chat.contact_service import serialize_message

        redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            redis.publish(
                LIVE_CHAT_EVENTS_CHANNEL,
                json.dumps(
                    {
                        "tenant_id": row.tenant_id,
                        "payload": {
                            "type": "live_chat_message",
                            "direction": "out",
                            "contact": row.phone,
                            "message": serialize_message(row),
                        },
                    },
                    ensure_ascii=True,
                ),
            )
        finally:
            redis.close()
    except Exception:
        log.exception("Failed to publish saved live chat message")
