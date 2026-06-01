from sqlalchemy import Column, Integer, String

from app.db.mixins import TimestampMixin
from app.db.session import Base


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


class ShopifyCatalogDefaultCategory(TimestampMixin, Base):
    __tablename__ = "shopify_catalog_default_categories"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    connection_id = Column(Integer, index=True, nullable=False)
    category_key = Column(String, index=True, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    visible = Column(String, default="true", index=True)
    sort_order = Column(Integer, default=0)
