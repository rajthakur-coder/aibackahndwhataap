from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.session import Base


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True)
    name = Column(String, nullable=True)
    profile_name = Column(String, nullable=True)
    custom_name = Column(String, nullable=True)
    remark = Column(Text, nullable=True)
    status = Column(String, default="Active", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contact_tags = relationship(
        "ContactTag",
        back_populates="contact",
        cascade="all, delete-orphan",
    )


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, index=True)
    color = Column(String(20), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String, default="Active", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    contact_tags = relationship(
        "ContactTag",
        back_populates="tag",
        cascade="all, delete-orphan",
    )

    __table_args__ = (UniqueConstraint("name", name="uq_tags_name"),)


class ContactTag(Base):
    __tablename__ = "contact_tags"

    id = Column(Integer, primary_key=True, index=True)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    contact = relationship("Contact", back_populates="contact_tags")
    tag = relationship("Tag", back_populates="contact_tags")

    __table_args__ = (UniqueConstraint("contact_id", "tag_id", name="uq_contact_tag"),)
