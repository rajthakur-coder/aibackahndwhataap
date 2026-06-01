from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


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
