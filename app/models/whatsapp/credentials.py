from sqlalchemy import Column, Integer, String, Text, Uuid

from app.db.mixins import TimestampMixin
from app.db.session import Base


class WhatsappCredential(TimestampMixin, Base):
    __tablename__ = "whatsapp_credentials"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Uuid(as_uuid=True), nullable=True, index=True)
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
    alignchat_callback_url = Column(Text, nullable=True)
