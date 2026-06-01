import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ecommerce import EcommerceOrder, EcommerceProduct
from app.modules.ecommerce.catalog.catalog_cache_service import (
    find_cached_catalog_categories,
    find_cached_catalog_products,
    find_cached_top_selling_products,
)
from app.modules.ecommerce.catalog import catalog_cache_service
from app.shared.tenant import reset_current_tenant_id, set_current_tenant_id


def _session():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EcommerceProduct.__table__.create(bind=engine)
    EcommerceOrder.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_generic_catalog_cache_uses_current_tenant_and_all_platforms():
    db = _session()
    db.add(
        EcommerceProduct(
            tenant_id="brand-a",
            connection_id=1,
            platform="woocommerce",
            external_id="woo-1",
            title="Ceramic Dinner Set",
            sku="DINNER-1",
            product_type="Tableware",
            tags="home ceramic dining",
            price_min="2499",
        )
    )
    db.add(
        EcommerceProduct(
            tenant_id="brand-b",
            connection_id=2,
            platform="shopify",
            external_id="shop-1",
            title="Running Shoes",
            sku="SHOE-1",
            product_type="Footwear",
            tags="sneaker shoe",
            price_min="2999",
        )
    )
    db.add(
        EcommerceOrder(
            tenant_id="brand-a",
            connection_id=1,
            platform="woocommerce",
            external_id="order-1",
            order_number="#1",
            items='[{"name": "Ceramic Dinner Set", "sku": "DINNER-1", "product_id": "woo-1", "quantity": 2}]',
        )
    )
    db.commit()

    token = set_current_tenant_id("brand-a")
    try:
        categories = asyncio.run(find_cached_catalog_categories(db, limit=10))
        products = asyncio.run(find_cached_catalog_products(db, "ceramic", limit=5))
        top = asyncio.run(find_cached_top_selling_products(db, limit=5))
    finally:
        reset_current_tenant_id(token)

    assert categories[0]["title"] == "Tableware"
    assert [item["title"] for item in products] == ["Ceramic Dinner Set"]
    assert products[0]["platform"] == "woocommerce"
    assert [item["title"] for item in top] == ["Ceramic Dinner Set"]


def test_catalog_cache_delegates_categories_to_shopify_runtime(monkeypatch):
    db = _session()

    class ShopifyRuntime:
        async def find_cached_catalog_categories(self, db, limit=10, phone=None):
            return [
                {
                    "id": "catalog:category:collection_living_room",
                    "title": "Living Room",
                    "description": "4 products",
                }
            ]

    monkeypatch.setattr(catalog_cache_service, "active_shopify_connection", lambda db, phone=None: object())
    monkeypatch.setattr(catalog_cache_service, "_shopify_catalog_runtime", lambda: ShopifyRuntime())

    categories = asyncio.run(find_cached_catalog_categories(db, limit=10, phone="919999999999"))

    assert categories == [
        {
            "id": "catalog:category:collection_living_room",
            "title": "Living Room",
            "description": "4 products",
        }
    ]
