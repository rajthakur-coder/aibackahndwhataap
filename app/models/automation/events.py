from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AutomationEvent(TimestampMixin, Base):
    __tablename__ = "automation_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    trigger = Column(String, index=True, nullable=False)
    source = Column(String, default="system", index=True)
    external_id = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True, index=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    scheduled_for = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)
