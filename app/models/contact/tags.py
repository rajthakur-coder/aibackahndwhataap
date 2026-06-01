from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Tag(TimestampMixin, Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    name = Column(String(100), nullable=False, index=True)
    color = Column(String(20), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String, default="Active", index=True)
    contact_tags = relationship(
        "ContactTag",
        back_populates="tag",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tags_tenant_name"),)


class ContactTag(TimestampMixin, Base):
    __tablename__ = "contact_tags"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    contact = relationship("Contact", back_populates="contact_tags")
    tag = relationship("Tag", back_populates="contact_tags")

    __table_args__ = (UniqueConstraint("tenant_id", "contact_id", "tag_id", name="uq_contact_tag_tenant"),)
