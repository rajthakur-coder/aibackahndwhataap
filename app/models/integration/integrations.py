from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import relationship

from app.db.mixins import TimestampMixin
from app.db.session import Base
from app.models.integration.constants import IntegrationStatus


class Integration(TimestampMixin, Base):
    __tablename__ = "ai_integrations"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid4, index=True)
    user_id = Column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tenant_id = Column(String(80), nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)
    status = Column(String, default=IntegrationStatus.CONNECTED, nullable=False, index=True)
    scopes = Column(Text, nullable=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    provider_account_id = Column(String, nullable=True, index=True)
    display_name = Column(String, nullable=True)

    user = relationship("User", back_populates="integrations")
