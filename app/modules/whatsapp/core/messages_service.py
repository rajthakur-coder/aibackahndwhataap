from sqlalchemy.orm import Session

from app.models.whatsapp import Message


def save_message(
    db: Session,
    phone: str,
    message: str,
    direction: str,
    whatsapp_message_id: str | None = None,
) -> Message:
    row = Message(
        phone=phone,
        message=message,
        direction=direction,
        status="received" if direction == "incoming" else "sent",
        message_type="text",
        whatsapp_message_id=whatsapp_message_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
