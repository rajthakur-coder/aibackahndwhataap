from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AuditLog(TimestampMixin, Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, index=True, nullable=True)
    user_id = Column(String, index=True, nullable=True)
    action = Column(String, index=True, nullable=False)
    entity_type = Column(String, index=True, nullable=True)
    entity_id = Column(String, index=True, nullable=True)
    status = Column(String, default="success", index=True)
    request_id = Column(String, index=True, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
