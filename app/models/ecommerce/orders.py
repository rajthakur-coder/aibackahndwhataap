from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceOrder(TimestampMixin, Base):
    __tablename__ = "ecommerce_orders"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True, nullable=False)
    shopify_order_id = Column(String, nullable=True, index=True)
    ecommerce_customer_id = Column(Integer, nullable=True, index=True)
    order_number = Column(String, index=True, nullable=False)
    phone = Column(String, index=True, nullable=True)
    email = Column(String, index=True, nullable=True)
    customer_name = Column(String, nullable=True)
    tags = Column(Text, nullable=True)
    note = Column(Text, nullable=True)
    shipping_address = Column(Text, nullable=True)
    billing_address = Column(Text, nullable=True)
    status = Column(String, index=True, nullable=True)
    fulfillment_status = Column(String, index=True, nullable=True)
    financial_status = Column(String, nullable=True)
    subtotal = Column(String, nullable=True)
    total = Column(String, nullable=True)
    discounts = Column(String, nullable=True)
    tax = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    payment_gateway = Column(String, nullable=True)
    skus = Column(Text, nullable=True)
    product_ids = Column(Text, nullable=True)
    tracking_number = Column(String, nullable=True)
    tracking_url = Column(Text, nullable=True)
    tracking_numbers = Column(Text, nullable=True)
    tracking_urls = Column(Text, nullable=True)
    courier_company = Column(String, nullable=True)
    shipment_status = Column(String, nullable=True)
    delivery_status = Column(String, nullable=True)
    items = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    shopify_created_at = Column(String, nullable=True)
    shopify_updated_at = Column(String, nullable=True)
    delivered_message_sent_at = Column(DateTime, nullable=True)
