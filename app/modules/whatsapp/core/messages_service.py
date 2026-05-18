from sqlalchemy.orm import Session

from app.models.whatsapp import Message


def save_message(db: Session, phone: str, message: str, direction: str) -> Message:
    row = Message(
        phone=phone,
        message=message,
        direction=direction,
        status="received" if direction == "incoming" else "sent",
        message_type="text",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
