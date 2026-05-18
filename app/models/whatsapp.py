from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    direction = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String, default="sent", index=True)
    message_type = Column(String, default="text")
    whatsapp_message_id = Column(String, nullable=True, unique=True, index=True)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, default="whatsapp", index=True)
    external_id = Column(String, unique=True, index=True, nullable=True)
    phone = Column(String, index=True, nullable=True)
    message_text = Column(Text, nullable=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class WhatsappCredential(TimestampMixin, Base):
    __tablename__ = "whatsapp_credentials"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", unique=True, index=True)
    waba_id = Column(String, nullable=True, index=True)
    business_id = Column(String, nullable=True, index=True)
    authorization_token = Column(Text, nullable=True)
    phone_number_id = Column(String, nullable=True, unique=True, index=True)
    phone_number = Column(String, nullable=True, unique=True, index=True)
    token = Column(Text, nullable=True)
    status = Column(String, default="active", index=True)
    business_name = Column(String, nullable=True)
    verified_name = Column(String, nullable=True)
    name = Column(String, nullable=True)
    callback_url = Column(Text, nullable=True)
    nerochat_callback_url = Column(Text, nullable=True)


class WhatsappTemplate(TimestampMixin, Base):
    __tablename__ = "whatsapp_templates"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone_number = Column(String, nullable=True, index=True)
    waba_id = Column(String, nullable=True, index=True)
    wa_template_id = Column(String, nullable=True, unique=True, index=True)
    name = Column(String, nullable=False, index=True)
    language = Column(String, nullable=False, index=True)
    language_name = Column(String, nullable=True)
    category = Column(String, nullable=False, index=True)
    parameter_format = Column(String, nullable=True)
    components = Column(Text, nullable=False)
    status = Column(String, default="PENDING", index=True)
    quality_rating = Column(String, nullable=True)
    message_send_ttl_seconds = Column(Integer, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "language", name="uq_whatsapp_template_name_language"),
    )
