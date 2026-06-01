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
from app.modules.ecommerce.catalog.shopify_catalog_text_runtime import *

from app.modules.ecommerce.catalog.shopify_catalog_presenter_runtime import *
from app.modules.ecommerce.catalog.shopify_catalog_sales_runtime import *
from app.modules.ecommerce.catalog.shopify_catalog_scoring_runtime import *

async def _rank_cached_shopify_products(
    db: Session,
    query: str,
    limit: int,
    require_image: bool = False,
    allow_fallback: bool = False,
    entities: dict | None = None,
    phone: str | None = None,
) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []
    limit = max(1, min(limit, 10))
    query_key = _query_cache_key(connection.id, query, limit, require_image, allow_fallback, entities)
    cached = await _redis_get_json(query_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_shopify_products(db, phone=phone)
    if require_image:
        products = [product for product in products if product.get("image_url")]
    if not products:
        return []

    search_text = _structured_search_text(query, entities)
    query_terms = search_terms(search_text)
    budget_max = _budget_max(query, entities)
    scored = []
    for product in products:
        if _hide_out_of_stock(query, entities) and not product.get("in_stock"):
            continue
        price = _product_price_number(product)
        if budget_max and price and price > budget_max:
            continue
        score = score_search_text(query_terms, product_search_text(product))
        score += _structured_entity_score(product, entities)
        score += _inventory_score(product)
        if budget_max and price:
            score += max(0.0, 1.0 - (price / budget_max))
        if product.get("image_url"):
            score += 0.15
        if product.get("product_url"):
            score += 0.1
        scored.append((score, product))

    ranked = [
        product
        for score, product in sorted(scored, key=lambda item: item[0], reverse=True)
        if score > 0
    ]
    if allow_fallback and not ranked:
        ranked = [product for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)]

    if not ranked and _hide_out_of_stock(query, entities):
        ranked = await _rank_cached_shopify_products(
            db,
            query,
            limit,
            require_image=require_image,
            allow_fallback=allow_fallback,
            entities={**(entities or {}), "_allow_out_of_stock": True},
            phone=phone,
        )

    result = ranked[:limit]
    if result:
        await _redis_set_json(query_key, result, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    return result






















__all__ = [
    "_rank_cached_shopify_products",
    "_matching_order",
    "_digits",
    "_top_selling_from_orders",
    "_frequently_bought_together_products",
    "_frequently_bought_together_index",
    "_line_item_identity_keys",
    "_product_identity_keys",
    "_product_lookup",
    "_quantity",
    "_order_status_text",
    "_product_result",
    "_price_range",
    "_product_caption",
    "_first_retailer_id",
    "_structured_search_text",
    "_structured_entity_score",
    "_hide_out_of_stock",
    "_inventory_score",
    "_budget_max",
    "_product_price_number",
    "_price_number",
]
