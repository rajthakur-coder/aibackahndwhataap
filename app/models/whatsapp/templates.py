from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


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
