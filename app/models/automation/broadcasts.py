from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class BroadcastCampaign(TimestampMixin, Base):
    __tablename__ = "broadcast_campaigns"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    name = Column(String, index=True, nullable=False)
    template = Column(String, index=True, nullable=False)
    audience = Column(Text, nullable=False)
    variables = Column(Text, nullable=True)
    status = Column(String, default="queued", index=True)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_by = Column(String, nullable=True)
