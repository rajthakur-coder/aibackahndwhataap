from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    direction = Column(String, nullable=False)
    status = Column(String, default="sent", index=True)
    message_type = Column(String, default="text")
    payload = Column(Text, nullable=True)
    whatsapp_message_id = Column(String, nullable=True, unique=True, index=True)
