from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class ShopifyWebhookEvent(TimestampMixin, Base):
    __tablename__ = "shopify_webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=True)
    shop_domain = Column(String, index=True, nullable=False)
    topic = Column(String, index=True, nullable=False)
    webhook_id = Column(String, unique=True, index=True, nullable=True)
    request_id = Column(String, index=True, nullable=True)
    payload_hash = Column(String, index=True, nullable=False)
    status = Column(String, default="pending", index=True)
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    dead_lettered_at = Column(DateTime, nullable=True, index=True)
    raw_payload = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)
