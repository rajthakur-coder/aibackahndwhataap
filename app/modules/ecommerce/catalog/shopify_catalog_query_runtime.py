import json
import re
import time
from collections import Counter, defaultdict

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models.ecommerce import (
    EcommerceConnection,
    ShopifyCatalogCollection,
)
from app.modules.ai.search.product_search_service import product_search_text, score_search_text, search_terms
from app.modules.ai.recommendations.sales_recommendations_service import is_sales_recommendation_request
from app.modules.ecommerce.orders.order_service import _normalize_order
from app.modules.ecommerce.catalog.product_service import _normalize_product
from app.modules.ecommerce.providers.shopify.client_service import (
    fetch_all_products,
    fetch_order_by_number,
    fetch_orders,
    fetch_orders_for_sales,
    fetch_shopify_collections,
    fetch_shopify_collects,
)
from app.modules.ecommerce.providers.shopify.connection_lookup_service import (
    active_shopify_connection as _active_shopify_connection,
)
from app.shared.redis import get_redis


PRODUCT_CATALOG_CACHE_LIMIT = 5000
ORDER_CACHE_LIMIT = 100
TOP_SELLING_ORDER_LIMIT = 500
LOCAL_QUERY_CACHE_TTL_SECONDS = 60
_local_query_cache: dict[str, tuple[float, object]] = {}
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b", re.I)
TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
NON_CATEGORY_LABEL_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?\s*%?\s*)?(?:gst|igst|cgst|sgst|vat|tax|taxable|non\s*taxable)(?:\s*\d+(?:\.\d+)?\s*%?)?$",
    re.I,
)
IMAGE_REQUEST_TERMS = {
    "image",
    "images",
    "photo",
    "photos",
    "pic",
    "pics",
    "picture",
    "pictures",
    "tasveer",
    "tasvir",
    "dikha",
    "dikhana",
    "dikhao",
    "bhejo",
}
CATALOG_REQUEST_TERMS = {
    "catalog",
    "catalogue",
    "products",
    "product",
    "collection",
    "collections",
    "items",
    "list",
    "menu",
    "range",
}
REQUEST_ACTION_TERMS = {"bhejo", "chahiye", "chaiye", "dekhna", "dikha", "dikhana", "dikhao", "send", "show"}


from app.modules.ecommerce.catalog.shopify_catalog_cache_runtime import *
from app.modules.ecommerce.catalog.shopify_catalog_ranking_runtime import *
from app.modules.ecommerce.catalog.shopify_catalog_text_runtime import *

async def find_cached_default_catalog_categories(
    db: Session,
    phone: str | None = None,
) -> list[dict] | None:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return None
    return selected_default_catalog_category_rows(db, connection.id)

async def find_cached_order_status(
    db: Session,
    phone: str,
    query: str,
) -> str | None:
    order_id = _extract_order_id(query)
    cache_key = f"shopify:order-status:v1:{phone}:{order_id or 'latest'}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, str):
        return cached

    order = await _cached_shopify_order_by_id(db, order_id, phone=phone) if order_id else None
    if not order:
        orders = await _cached_shopify_orders(db, phone=phone)
        if not orders:
            return None
        order = _matching_order(orders, phone, order_id)
    if not order:
        return None

    reply = _order_status_text(order)
    await _redis_set_json(cache_key, reply, settings.SHOPIFY_ORDER_CACHE_TTL_SECONDS)
    return reply


async def find_cached_product_recommendations(
    db: Session,
    query: str,
    limit: int = 5,
    entities: dict | None = None,
    phone: str | None = None,
) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit, entities=entities, phone=phone)
    return products


async def find_cached_cross_sell_products(
    db: Session,
    query: str,
    base_products: list[dict],
    limit: int = 3,
    phone: str | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 5))
    if not base_products:
        return []
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    signature = ",".join(
        sorted(
            str(product.get("external_id") or product.get("shopify_product_id") or product.get("sku") or product.get("title") or "")
            for product in base_products
        )
    )[:160]
    cache_key = f"shopify:cross-sell:v3:{connection.id}:limit:{limit}:{signature}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_all_shopify_products(db, phone=phone)
    if not products:
        return []

    result = await _frequently_bought_together_products(db, base_products, products, limit, phone=phone)
    if result:
        await _redis_set_json(cache_key, result, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
        return result

    exclude_titles = {str(product.get("title") or "").strip().lower() for product in base_products}
    exclude_ids = {
        str(product.get(key) or "").strip().lower()
        for product in base_products
        for key in ("external_id", "shopify_product_id", "sku", "retailer_id")
        if product.get(key)
    }
    terms = _cross_sell_terms(query, base_products)
    if not terms:
        return []

    query_terms = search_terms(" ".join(sorted(terms)))
    scored = []
    for product in products:
        title_key = str(product.get("title") or "").strip().lower()
        if title_key in exclude_titles:
            continue
        product_ids = {
            str(product.get(key) or "").strip().lower()
            for key in ("external_id", "shopify_product_id", "sku", "retailer_id")
            if product.get(key)
        }
        if product_ids & exclude_ids:
            continue

        searchable = " ".join(
            str(product.get(key) or "")
            for key in ("title", "description", "category", "product_type", "tags", "brand", "sku")
        )
        score = score_search_text(query_terms, searchable)
        if product.get("image_url"):
            score += 0.15
        if product.get("product_url"):
            score += 0.1
        if score > 0:
            scored.append((score, product))

    result = [product for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)][:limit]
    if result:
        await _redis_set_json(cache_key, result, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    return result


async def find_cached_top_selling_products(
    db: Session,
    limit: int = 3,
    phone: str | None = None,
) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    limit = max(1, min(limit, 10))
    cache_key = f"shopify:top-selling:v1:{connection.id}:limit:{limit}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    orders = await _cached_shopify_sales_orders(db, phone=phone)
    if not orders:
        return []

    products = await _cached_all_shopify_products(db, phone=phone)
    ranked = _top_selling_from_orders(orders, products, limit)
    if ranked:
        await _redis_set_json(cache_key, ranked, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    return ranked


async def find_cached_catalog_products(
    db: Session,
    query: str,
    limit: int = 5,
    entities: dict | None = None,
    phone: str | None = None,
) -> list[dict]:
    if not is_catalog_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit, allow_fallback=True, entities=entities, phone=phone)
    return products


async def find_cached_category_products(
    db: Session,
    category: str,
    limit: int = 5,
    offset: int = 0,
    phone: str | None = None,
) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []
    limit = max(1, min(limit, 10))
    offset = max(0, offset)
    category_key = " ".join((category or "").lower().split())[:80]
    if not category_key:
        return []

    cache_key = f"shopify:category:v3:{connection.id}:{category_key}:limit:{limit}:offset:{offset}"
    local_cached = _local_cache_get(cache_key)
    if isinstance(local_cached, list):
        return local_cached
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        _local_cache_set(cache_key, cached)
        return cached

    products = await _cached_all_shopify_products(db, phone=phone)
    if not products:
        return []

    collection_products = await _cached_shopify_collection_products(db, category_key, phone=phone)
    if collection_products:
        result = collection_products[offset : offset + limit]
    elif category_key in {"all", "all products", "new", "new arrivals"}:
        result = products[offset : offset + limit]
    else:
        query_terms = search_terms(category_key)
        scored = []
        for product in products:
            haystack = _category_product_haystack(product)
            score = score_search_text(query_terms, haystack)
            if score > 0:
                scored.append((score, product))
        result = [
            product
            for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)
        ][offset : offset + limit]

    if result:
        await _redis_set_json(cache_key, result, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
        _local_cache_set(cache_key, result)
    return result


async def find_cached_catalog_categories(
    db: Session,
    limit: int = 24,
    phone: str | None = None,
) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []
    limit = max(1, min(limit, 50))
    cache_key = f"shopify:categories:v7:{connection.id}:limit:{limit}"
    local_cached = _local_cache_get(cache_key)
    if isinstance(local_cached, list) and local_cached:
        return local_cached
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list) and cached:
        _local_cache_set(cache_key, cached)
        return cached

    collection_categories = await _cached_shopify_collection_categories(db, phone=phone)
    if collection_categories:
        categories = collection_categories[:limit]
        await _redis_set_json(cache_key, categories, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
        _local_cache_set(cache_key, categories)
        return categories

    products = await _cached_all_shopify_products(db, phone=phone)
    if not products:
        return []

    counts: Counter[str] = Counter()
    exact_counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    excluded_labels = _store_labels(products)
    for product in products:
        for label in _category_labels(product, excluded_labels):
            slug = _category_slug(label)
            if not slug:
                continue
            exact_counts[slug] += 1
            labels.setdefault(slug, label.strip()[:24])

    for slug, label in labels.items():
        query_terms = search_terms(label)
        if not query_terms:
            continue
        for product in products:
            if score_search_text(query_terms, _category_product_haystack(product)) > 0:
                counts[slug] += 1

    for slug, count in exact_counts.items():
        counts[slug] = max(counts[slug], count)

    categories = [
        {
            "id": f"catalog:category:{slug}",
            "title": labels[slug],
            "description": f"{count} products",
        }
        for slug, count in counts.most_common(limit)
        if count > 0
    ]
    await _redis_set_json(cache_key, categories, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    _local_cache_set(cache_key, categories)
    return categories


async def find_cached_product_image(
    db: Session,
    query: str,
    entities: dict | None = None,
    phone: str | None = None,
) -> dict | None:
    if not is_image_request(query):
        return None
    products = await _rank_cached_shopify_products(
        db,
        query,
        1,
        require_image=True,
        allow_fallback=True,
        entities=entities,
        phone=phone,
    )
    return products[0] if products else None






























































































