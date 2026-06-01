from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AutomationRule(TimestampMixin, Base):
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    trigger = Column(String, index=True, nullable=False)
    message_template_id = Column(Integer, nullable=True, index=True)
    message_body = Column(Text, nullable=True)
    delay_seconds = Column(Integer, default=0)
    conditions = Column(Text, nullable=True)
    enabled = Column(String, default="true", index=True)
