from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class CustomerProfile(TimestampMixin, Base):
    __tablename__ = "customer_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_customer_profiles_tenant_phone"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="active")


class CustomerMemory(TimestampMixin, Base):
    __tablename__ = "customer_memories"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=False)
    memory_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
