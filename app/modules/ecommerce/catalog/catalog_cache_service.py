from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.ecommerce import EcommerceProduct
from app.modules.ai.recommendations.sales_recommendations_service import (
    find_cross_sell_products,
    find_product_recommendations,
    find_top_selling_products,
)
from app.modules.ai.search.product_search_service import product_search_text, score_search_text, search_terms
from app.modules.ecommerce.providers.shopify.connection_lookup_service import active_shopify_connection
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


CATALOG_TERMS = {"catalog", "catalogue", "product", "products", "collection", "collections", "items", "menu"}
IMAGE_TERMS = {"image", "images", "photo", "photos", "pic", "picture", "tasveer", "tasvir"}


def is_catalog_request(text: str) -> bool:
    return bool(set(_tokens(text)) & CATALOG_TERMS)


def is_image_request(text: str) -> bool:
    return bool(set(_tokens(text)) & IMAGE_TERMS)


async def find_cached_default_catalog_categories(db: Session, phone: str | None = None) -> list[dict] | None:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_default_catalog_categories(db, phone=phone)
    return None


async def find_cached_catalog_categories(db: Session, limit: int = 10, phone: str | None = None) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_catalog_categories(db, limit=limit, phone=phone)

    tenant_id = _tenant_id()

    def sync_op() -> list[dict]:
        rows = db.execute(
            select(EcommerceProduct)
            .where(EcommerceProduct.tenant_id == tenant_id)
            .order_by(EcommerceProduct.updated_at.desc())
            .limit(500)
        ).scalars().all()
        seen: dict[str, dict] = {}
        for row in rows:
            for label in _category_labels(row):
                key = _category_key(label)
                if not key or key in seen:
                    continue
                seen[key] = {
                    "id": f"catalog:category:{key}",
                    "title": label[:24],
                    "description": f"Browse {label}"[:72],
                    "category_key": key,
                    "sort_order": len(seen) + 10,
                }
        return list(seen.values())[: max(1, min(limit, 50))]

    return await run_in_threadpool(sync_op)


async def find_cached_category_products(
    db: Session,
    category: str,
    limit: int = 5,
    offset: int = 0,
    phone: str | None = None,
) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_category_products(
            db,
            category,
            limit=limit,
            offset=offset,
            phone=phone,
        )

    tenant_id = _tenant_id()

    def sync_op() -> list[dict]:
        rows = db.execute(
            select(EcommerceProduct)
            .where(EcommerceProduct.tenant_id == tenant_id)
            .order_by(EcommerceProduct.updated_at.desc())
            .limit(500)
        ).scalars().all()
        key = _category_key(category)
        if key and key != "all":
            rows = [row for row in rows if key in {_category_key(label) for label in _category_labels(row)}]
        start = max(0, offset)
        end = start + max(1, min(limit, 20))
        return [_product_payload(row) for row in rows[start:end]]

    return await run_in_threadpool(sync_op)


async def find_cached_catalog_products(
    db: Session,
    query: str,
    limit: int = 5,
    entities: dict | None = None,
    phone: str | None = None,
) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_catalog_products(
            db,
            query,
            limit=limit,
            entities=entities,
            phone=phone,
        )

    tenant_id = _tenant_id()

    def sync_op() -> list[dict]:
        rows = db.execute(
            select(EcommerceProduct)
            .where(EcommerceProduct.tenant_id == tenant_id)
            .order_by(EcommerceProduct.updated_at.desc())
            .limit(500)
        ).scalars().all()
        terms = search_terms(" ".join([query or "", *_entity_terms(entities or {})]))
        scored = sorted(
            ((score_search_text(terms, product_search_text(row)), row) for row in rows),
            key=lambda item: item[0],
            reverse=True,
        )
        ranked = [row for score, row in scored if score > 0] or rows
        return [_product_payload(row) for row in ranked[: max(1, min(limit, 20))]]

    return await run_in_threadpool(sync_op)


async def find_cached_product_recommendations(
    db: Session,
    query: str,
    limit: int = 5,
    entities: dict | None = None,
    phone: str | None = None,
) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_product_recommendations(
            db,
            query,
            limit=limit,
            entities=entities,
            phone=phone,
        )

    tenant_id = _tenant_id()
    return await run_in_threadpool(lambda: find_product_recommendations(db, query, limit=limit, tenant_id=tenant_id))


async def find_cached_cross_sell_products(
    db: Session,
    query: str,
    base_products: list[dict],
    limit: int = 3,
    phone: str | None = None,
) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_cross_sell_products(
            db,
            query,
            base_products,
            limit=limit,
            phone=phone,
        )

    tenant_id = _tenant_id()
    return await run_in_threadpool(
        lambda: find_cross_sell_products(db, query, base_products, limit=limit, tenant_id=tenant_id)
    )


async def find_cached_top_selling_products(db: Session, limit: int = 5, phone: str | None = None) -> list[dict]:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_top_selling_products(db, limit=limit, phone=phone)

    tenant_id = _tenant_id()
    return await run_in_threadpool(lambda: find_top_selling_products(db, limit=limit, tenant_id=tenant_id))


async def find_cached_product_image(
    db: Session,
    query: str,
    entities: dict | None = None,
    phone: str | None = None,
) -> dict | None:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_product_image(db, query, entities=entities, phone=phone)

    products = await find_cached_catalog_products(db, query, limit=1, phone=phone)
    return products[0] if products and products[0].get("image_url") else None


async def find_cached_order_status(db: Session, phone: str, query: str) -> dict | None:
    if _has_active_shopify_connection(db, phone):
        shopify_catalog = _shopify_catalog_runtime()
        return await shopify_catalog.find_cached_order_status(db, phone, query)

    return None


def _has_active_shopify_connection(db: Session, phone: str | None = None) -> bool:
    try:
        return active_shopify_connection(db, phone=phone) is not None
    except SQLAlchemyError:
        return False


def _shopify_catalog_runtime():
    from app.modules.ecommerce.catalog import shopify_catalog_query_runtime

    return shopify_catalog_query_runtime


def _tenant_id() -> str:
    return normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)


def _product_payload(row: EcommerceProduct) -> dict:
    images = _json_list(row.image_urls)
    return {
        "source": "catalog_cache",
        "tenant_id": row.tenant_id,
        "platform": row.platform,
        "title": row.title,
        "description": row.description,
        "category": row.product_type,
        "brand": row.vendor,
        "tags": row.tags,
        "product_type": row.product_type,
        "price_min": row.price_min,
        "price_max": row.price_max,
        "price": _price_range(row.price_min, row.price_max),
        "product_url": row.product_url,
        "image_url": images[0] if images else None,
        "sku": row.sku,
        "external_id": row.external_id,
        "retailer_id": row.sku or row.external_id,
    }


def _category_labels(row: EcommerceProduct) -> list[str]:
    labels = []
    if row.product_type:
        labels.append(str(row.product_type))
    for item in _json_list(row.collections):
        labels.append(str(item))
    for tag in re.split(r"[,|]", row.tags or ""):
        tag = tag.strip()
        if tag and len(tag) <= 32:
            labels.append(tag)
    return list(dict.fromkeys(label for label in labels if label))


def _category_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")[:48]


def _entity_terms(entities: dict) -> list[str]:
    values = []
    for key in ("category", "product_type", "color", "size", "material", "use_case"):
        if entities.get(key):
            values.append(str(entities[key]))
    attributes = entities.get("attributes")
    if isinstance(attributes, list):
        values.extend(str(item) for item in attributes)
    return values


def _json_list(value: str | None) -> list:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _price_range(price_min: str | None, price_max: str | None) -> str:
    if price_min and price_max and price_min != price_max:
        return f"{price_min} - {price_max}"
    return price_min or price_max or ""


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[a-zA-Z0-9]+", text or "")]


find_cached_catalog_categories = find_cached_catalog_categories
find_cached_catalog_products = find_cached_catalog_products
find_cached_category_products = find_cached_category_products
find_cached_cross_sell_products = find_cached_cross_sell_products
find_cached_default_catalog_categories = find_cached_default_catalog_categories
find_cached_order_status = find_cached_order_status
find_cached_product_image = find_cached_product_image
find_cached_product_recommendations = find_cached_product_recommendations
find_cached_top_selling_products = find_cached_top_selling_products


__all__ = [
    "find_cached_catalog_categories",
    "find_cached_catalog_products",
    "find_cached_category_products",
    "find_cached_cross_sell_products",
    "find_cached_default_catalog_categories",
    "find_cached_order_status",
    "find_cached_product_image",
    "find_cached_product_recommendations",
    "find_cached_top_selling_products",
    "is_catalog_request",
    "is_image_request",
]
