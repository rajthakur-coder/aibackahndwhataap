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


def _extract_order_id(query: str) -> str | None:
    match = ORDER_RE.search(query or "")
    return next((group.upper() for group in match.groups() if group), None) if match else None

def is_catalog_request(query: str) -> bool:
    terms = set(_tokens(query))
    return bool(terms & CATALOG_REQUEST_TERMS and terms & REQUEST_ACTION_TERMS)

def is_image_request(query: str) -> bool:
    return bool(set(_tokens(query)) & IMAGE_REQUEST_TERMS)

def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]

def _category_product_haystack(product: dict) -> str:
    return " ".join(
        str(product.get(key) or "")
        for key in ("title", "description", "category", "product_type", "tags", "brand", "sku")
    )

def _category_labels(product: dict, excluded_labels: set[str] | None = None) -> list[str]:
    labels = []
    excluded_labels = excluded_labels or set()
    for key in ("category", "product_type"):
        value = str(product.get(key) or "").strip()
        if value:
            labels.append(value)

    tags = product.get("tags")
    if isinstance(tags, str):
        labels.extend(tag.strip() for tag in re.split(r"[,|/]", tags) if tag.strip())
    elif isinstance(tags, list):
        labels.extend(str(tag).strip() for tag in tags if str(tag).strip())

    return [
        label
        for label in labels
        if _is_clean_category_label(label, excluded_labels)
    ]

def _store_labels(products: list[dict]) -> set[str]:
    counts: Counter[str] = Counter()
    total = max(1, len(products))
    for product in products:
        for key in ("brand", "vendor"):
            value = str(product.get(key) or "").strip().lower()
            if value:
                counts[value] += 1
    return {label for label, count in counts.items() if count >= 3 or count / total >= 0.05}

def _is_clean_category_label(label: str, excluded_labels: set[str]) -> bool:
    normalized = " ".join((label or "").lower().split())
    if not (2 <= len(label.strip()) <= 24):
        return False
    if normalized in excluded_labels:
        return False
    if NON_CATEGORY_LABEL_RE.match(normalized):
        return False
    if re.search(r"\b(?:gst|igst|cgst|sgst|vat|tax|taxable)\b", normalized):
        return False
    if normalized.startswith(("all ", "best ")):
        return False
    if normalized in {"my store", "store", "shop", "home senses", "the home senses"}:
        return False
    if normalized.endswith((" store", " shop")):
        return False
    return True

def _category_slug(label: str) -> str:
    return "_".join(TOKEN_RE.findall((label or "").lower()))[:80]

def _cross_sell_terms(query: str, base_products: list[dict]) -> set[str]:
    terms = set(search_terms(query))
    for product in base_products:
        for key in ("category", "product_type", "brand", "tags", "title"):
            terms.update(search_terms(str(product.get(key) or "")))

    expansions = {
        "shirt": {"jeans", "pants", "belt", "jacket"},
        "tshirt": {"jeans", "shorts", "jacket"},
        "shoe": {"socks", "laces", "cleaner"},
        "shoes": {"socks", "laces", "cleaner"},
        "phone": {"case", "cover", "charger"},
        "watch": {"strap", "band", "accessory"},
        "bag": {"wallet", "pouch", "accessory"},
        "dress": {"heels", "bag", "accessory"},
    }
    for term in list(terms):
        terms.update(expansions.get(term, set()))
    return {term for term in terms if len(term) > 2}

__all__ = [
    "_extract_order_id",
    "is_catalog_request",
    "is_image_request",
    "_tokens",
    "_category_product_haystack",
    "_category_labels",
    "_store_labels",
    "_is_clean_category_label",
    "_category_slug",
    "_cross_sell_terms",
]
