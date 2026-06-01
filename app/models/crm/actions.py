from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AgentAction(TimestampMixin, Base):
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=True)
    action_type = Column(String, index=True, nullable=False)
    status = Column(String, default="logged")
    payload = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
