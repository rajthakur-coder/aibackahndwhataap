from sqlalchemy.orm import Session

from app.models.entities import Message


def save_message(db: Session, phone: str, message: str, direction: str) -> Message:
    row = Message(phone=phone, message=message, direction=direction)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
