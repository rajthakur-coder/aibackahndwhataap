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

from app.modules.ecommerce.catalog.shopify_catalog_text_runtime import *

def _structured_search_text(query: str, entities: dict | None) -> str:
    parts = [query or ""]
    if not isinstance(entities, dict):
        return " ".join(parts)
    for key in ("product_type", "category", "brand", "color", "size", "material", "use_case"):
        value = entities.get(key)
        if value:
            parts.append(str(value))
    attributes = entities.get("attributes")
    if isinstance(attributes, list):
        parts.extend(str(value) for value in attributes if value)
    elif attributes:
        parts.append(str(attributes))
    return " ".join(parts)

def _structured_entity_score(product: dict, entities: dict | None) -> float:
    if not isinstance(entities, dict):
        return 0.0
    score = 0.0
    weighted_fields = {
        "product_type": 2.0,
        "category": 1.5,
        "brand": 1.2,
        "color": 1.0,
        "size": 0.8,
        "material": 1.0,
        "use_case": 1.0,
    }
    product_text = product_search_text(product)
    for key, weight in weighted_fields.items():
        value = entities.get(key)
        if not value:
            continue
        score += score_search_text(search_terms(str(value)), product_text) * weight

    attributes = entities.get("attributes")
    if isinstance(attributes, list):
        attribute_text = " ".join(str(value) for value in attributes if value)
    else:
        attribute_text = str(attributes or "")
    if attribute_text:
        score += score_search_text(search_terms(attribute_text), product_text)
    return score

def _hide_out_of_stock(query: str, entities: dict | None) -> bool:
    if isinstance(entities, dict) and entities.get("_allow_out_of_stock"):
        return False
    text = " ".join([query or "", json.dumps(entities or {}, ensure_ascii=True)]).lower()
    if any(term in text for term in ("out of stock", "sold out", "unavailable")):
        return False
    return True

def _inventory_score(product: dict) -> float:
    if product.get("in_stock") is False:
        return -5.0
    score = 1.0 if product.get("in_stock") else 0.0
    quantity = product.get("stock_quantity")
    if isinstance(quantity, (int, float)) and quantity > 0:
        score += min(float(quantity), 25.0) / 25.0
    return score

def _budget_max(query: str, entities: dict | None) -> float | None:
    if isinstance(entities, dict):
        for key in ("budget_max", "max_price", "price_max", "budget"):
            value = entities.get(key)
            parsed = _price_number(value)
            if parsed:
                return parsed
    budget_match = re.search(
        r"(?:under|below|less than|upto|up to|budget|andar|neeche|kam|<=?)\s*(?:rs\.?|inr|₹)?\s*([\d,]+)",
        query or "",
        re.I,
    )
    return _price_number(budget_match.group(1)) if budget_match else None

def _product_price_number(product: dict) -> float | None:
    return _price_number(product.get("price_min") or product.get("price") or product.get("price_max"))

def _price_number(value) -> float | None:
    if value is None:
        return None
    match = re.search(r"[\d,.]+", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None

__all__ = [
    "_structured_search_text",
    "_structured_entity_score",
    "_hide_out_of_stock",
    "_inventory_score",
    "_budget_max",
    "_product_price_number",
    "_price_number",
]
