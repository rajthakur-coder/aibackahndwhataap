from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class WebhookEvent(TimestampMixin, Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    provider = Column(String, default="whatsapp", index=True)
    external_id = Column(String, unique=True, index=True, nullable=True)
    request_id = Column(String, index=True, nullable=True)
    phone = Column(String, index=True, nullable=True)
    message_text = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    dead_lettered_at = Column(DateTime, nullable=True, index=True)
    processed_at = Column(DateTime, nullable=True)


class WhatsappInteractionEvent(TimestampMixin, Base):
    __tablename__ = "whatsapp_interaction_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=True)
    event_type = Column(String, index=True, nullable=False)
    source = Column(String, index=True, nullable=True)
    message_id = Column(String, index=True, nullable=True)
    interaction_id = Column(String, index=True, nullable=True)
    title = Column(String, nullable=True)
    target_url = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
