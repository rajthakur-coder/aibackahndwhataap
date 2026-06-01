from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class ContactStoreMapping(TimestampMixin, Base):
    __tablename__ = "contact_store_mappings"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    normalized_phone = Column(String, index=True, nullable=False)
    connection_id = Column(Integer, index=True, nullable=False)
    source = Column(String, default="auto", index=True)
    status = Column(String, default="active", index=True)
    last_seen_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_phone", name="uq_contact_store_mapping_tenant_phone"),
    )
