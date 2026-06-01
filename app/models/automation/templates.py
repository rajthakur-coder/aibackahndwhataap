from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class MessageTemplate(TimestampMixin, Base):
    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_message_templates_tenant_name"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    body = Column(Text, nullable=False)
    channel = Column(String, default="whatsapp", index=True)
    template_type = Column(String, default="text", index=True)
    provider_template_name = Column(String, nullable=True, index=True)
    language = Column(String, default="en")
    body_variable_order = Column(Text, nullable=True)
    status = Column(String, default="active", index=True)
