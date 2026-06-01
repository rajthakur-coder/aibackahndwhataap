from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceCustomer(TimestampMixin, Base):
    __tablename__ = "ecommerce_customers"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True, nullable=False)
    shopify_customer_id = Column(String, nullable=True, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, index=True, nullable=True)
    email = Column(String, index=True, nullable=True)
    total_orders = Column(Integer, default=0)
    total_spend = Column(String, nullable=True)
    tags = Column(Text, nullable=True)
    addresses = Column(Text, nullable=True)
    last_order_at = Column(String, nullable=True)
    marketing_consent = Column(String, nullable=True)
    preferred_language = Column(String, nullable=True)
    whatsapp_opt_in = Column(String, default="unknown")
    raw_payload = Column(Text, nullable=True)
