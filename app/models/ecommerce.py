from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceConnection(TimestampMixin, Base):
    __tablename__ = "ecommerce_connections"

    id = Column(Integer, primary_key=True, index=True)
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


class EcommerceProduct(TimestampMixin, Base):
    __tablename__ = "ecommerce_products"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    platform = Column(String, index=True, nullable=False)
    external_id = Column(String, index=True, nullable=False)
    shopify_product_id = Column(String, nullable=True, index=True)
    title = Column(String, index=True, nullable=False)
    handle = Column(String, nullable=True, index=True)
    product_url = Column(Text, nullable=True)
    description_html = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    vendor = Column(String, nullable=True)
    product_type = Column(String, nullable=True, index=True)
    tags = Column(Text, nullable=True)
    collections = Column(Text, nullable=True)
    status = Column(String, nullable=True, index=True)
    price_min = Column(String, nullable=True)
    price_max = Column(String, nullable=True)
    prices = Column(Text, nullable=True)
    compare_at_prices = Column(Text, nullable=True)
    currency = Column(String, nullable=True)
    sku = Column(String, nullable=True, index=True)
    skus = Column(Text, nullable=True)
    inventory = Column(String, nullable=True)
    variants = Column(Text, nullable=True)
    options = Column(Text, nullable=True)
    seo_title = Column(String, nullable=True)
    seo_description = Column(Text, nullable=True)
    image_urls = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)


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


class ShopifyCatalogCollection(TimestampMixin, Base):
    __tablename__ = "shopify_catalog_collections"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    shopify_collection_id = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)
    handle = Column(String, nullable=True, index=True)
    product_count = Column(Integer, default=0)
    visible = Column(String, default="true", index=True)
    sort_order = Column(Integer, default=0)


class ShopifyWebhookEvent(TimestampMixin, Base):
    __tablename__ = "shopify_webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=True)
    shop_domain = Column(String, index=True, nullable=False)
    topic = Column(String, index=True, nullable=False)
    webhook_id = Column(String, unique=True, index=True, nullable=True)
    payload_hash = Column(String, index=True, nullable=False)
    status = Column(String, default="pending", index=True)
    attempts = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)
