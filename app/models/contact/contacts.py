from sqlalchemy import Column, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Contact(TimestampMixin, Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True)
    name = Column(String, nullable=True)
    profile_name = Column(String, nullable=True)
    custom_name = Column(String, nullable=True)
    remark = Column(Text, nullable=True)
    status = Column(String, default="Active", index=True)
    contact_tags = relationship(
        "ContactTag",
        back_populates="contact",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("tenant_id", "phone", name="uq_contacts_tenant_phone"),)
