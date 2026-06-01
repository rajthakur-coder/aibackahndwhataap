from uuid import uuid4

from sqlalchemy import Boolean, Column, Integer, String, Uuid
from sqlalchemy.orm import relationship

from app.db.mixins import TimestampMixin
from app.db.session import Base


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password = Column(String, nullable=False)
    verified = Column(Boolean, default=False)
    credits = Column(Integer, default=50, nullable=False)
    onboarding_completed = Column(Boolean, default=False)

    integrations = relationship("Integration", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
