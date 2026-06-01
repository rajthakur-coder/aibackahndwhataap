from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, Uuid

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceConnection(TimestampMixin, Base):
    __tablename__ = "ecommerce_connections"

    id = Column(Integer, primary_key=True, index=True)
    integration_id = Column(Uuid(as_uuid=True), nullable=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    name = Column(String, nullable=False)
    platform = Column(String, index=True, nullable=False)
    store_url = Column(String, nullable=False)
    store_name = Column(String, nullable=True)
    myshopify_domain = Column(String, nullable=True, index=True)
    access_token = Column(Text, nullable=True)
    encrypted_access_token = Column(Text, nullable=True)
    consumer_key = Column(Text, nullable=True)
    consumer_secret = Column(Text, nullable=True)
    shopify_shop_id = Column(String, nullable=True, index=True)
    currency = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    owner_email = Column(String, nullable=True)
    owner_phone = Column(String, nullable=True)
    plan_name = Column(String, nullable=True)
    webhook_status = Column(String, default="pending", index=True)
    bot_enabled = Column(String, default="true", index=True)
    status = Column(String, default="active")
    installed_at = Column(DateTime, default=datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)
