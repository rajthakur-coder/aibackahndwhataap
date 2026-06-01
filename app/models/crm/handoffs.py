from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class HandoffTicket(TimestampMixin, Base):
    __tablename__ = "handoff_tickets"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, default="open")
    summary = Column(Text, nullable=True)
