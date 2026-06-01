from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AutomationExecution(TimestampMixin, Base):
    __tablename__ = "automation_executions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    event_id = Column(Integer, index=True, nullable=False)
    rule_id = Column(Integer, index=True, nullable=False)
    phone = Column(String, nullable=True, index=True)
    status = Column(String, default="pending", index=True)
    rendered_message = Column(Text, nullable=True)
    provider_response = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
