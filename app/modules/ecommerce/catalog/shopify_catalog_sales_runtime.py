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

from app.modules.ecommerce.catalog.shopify_catalog_cache_runtime import *
from app.modules.ecommerce.catalog.shopify_catalog_text_runtime import *

def _matching_order(orders: list[dict], phone: str, order_id: str | None) -> dict | None:
    normalized_order_id = (order_id or "").lstrip("#").upper()
    normalized_phone = _digits(phone)
    if order_id:
        for order in orders:
            candidates = {
                str(order.get("order_number") or "").lstrip("#").upper(),
                str(order.get("external_id") or "").upper(),
                str(order.get("shopify_order_id") or "").upper(),
            }
            if normalized_order_id in candidates:
                return order
        return None

    for order in orders:
        if normalized_phone and normalized_phone == _digits(order.get("phone")):
            return order
    return None

def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")

def _top_selling_from_orders(orders: list[dict], products: list[dict], limit: int) -> list[dict]:
    products_by_id = {
        str(product.get("shopify_product_id") or product.get("external_id") or ""): product
        for product in products
        if product.get("shopify_product_id") or product.get("external_id")
    }
    products_by_sku = {
        str(product.get("sku") or "").strip().lower(): product
        for product in products
        if product.get("sku")
    }
    products_by_title = {
        str(product.get("title") or "").strip().lower(): product
        for product in products
        if product.get("title")
    }
    sales: dict[tuple[str, str], dict] = {}

    for order in orders:
        for item in order.get("items") or []:
            quantity = _quantity(item.get("quantity"))
            if quantity <= 0:
                continue
            product_id = str(item.get("product_id") or "").strip()
            sku = str(item.get("sku") or "").strip()
            name = str(item.get("name") or "").strip()
            if product_id:
                key = ("product_id", product_id)
            elif sku:
                key = ("sku", sku.lower())
            elif name:
                key = ("name", name.lower())
            else:
                continue

            row = sales.setdefault(key, {"quantity": 0, "product_id": product_id, "sku": sku, "name": name})
            row["quantity"] += quantity

    ranked = []
    for (_key_type, key_value), row in sorted(sales.items(), key=lambda item: item[1]["quantity"], reverse=True):
        product = (
            products_by_id.get(str(row.get("product_id") or ""))
            or products_by_sku.get(str(row.get("sku") or "").lower())
            or products_by_title.get(str(row.get("name") or "").lower())
        )
        if product:
            result = dict(product)
        else:
            result = {
                "source": "shopify_orders_api",
                "title": row.get("name") or key_value,
                "sku": row.get("sku"),
                "shopify_product_id": row.get("product_id"),
                "price": "",
                "price_min": "",
                "price_max": "",
                "product_url": None,
                "image_url": None,
                "caption": row.get("name") or key_value,
            }
        result["sales_count"] = row["quantity"]
        ranked.append(result)
        if len(ranked) >= limit:
            break
    return ranked

async def _frequently_bought_together_products(
    db: Session,
    base_products: list[dict],
    products: list[dict],
    limit: int,
    phone: str | None = None,
) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    cache_key = f"shopify:fbt:v1:{connection.id}"
    index = await _redis_get_json(cache_key)
    if not isinstance(index, dict):
        orders = await _cached_shopify_sales_orders(db, phone=phone)
        index = _frequently_bought_together_index(orders)
        if index:
            await _redis_set_json(cache_key, index, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    if not index:
        return []

    product_lookup = _product_lookup(products)
    excluded_keys = _product_identity_keys(base_products)
    related_counts: Counter[str] = Counter()
    for key in _product_identity_keys(base_products):
        for related_key, count in (index.get(key) or {}).items():
            if related_key not in excluded_keys:
                related_counts[related_key] += int(count or 0)

    ranked = []
    seen_ids = set()
    for related_key, sales_count in related_counts.most_common():
        product = product_lookup.get(related_key)
        if not product:
            continue
        stable_id = str(product.get("shopify_product_id") or product.get("external_id") or product.get("title") or related_key)
        if stable_id in seen_ids:
            continue
        seen_ids.add(stable_id)
        if product.get("in_stock") is False:
            continue
        result = dict(product)
        result["fbt_count"] = sales_count
        ranked.append(result)
        if len(ranked) >= limit:
            break
    return ranked

def _frequently_bought_together_index(orders: list[dict]) -> dict:
    pairs: dict[str, Counter[str]] = defaultdict(Counter)
    for order in orders:
        item_keys = []
        for item in order.get("items") or []:
            keys = _line_item_identity_keys(item)
            if keys:
                item_keys.append(keys)
        if len(item_keys) < 2:
            continue
        for index, keys in enumerate(item_keys):
            related_key_sets = item_keys[:index] + item_keys[index + 1 :]
            for key in keys:
                for related_keys in related_key_sets:
                    for related_key in related_keys:
                        if related_key != key:
                            pairs[key][related_key] += 1
    return {key: dict(counter.most_common(20)) for key, counter in pairs.items() if counter}

def _line_item_identity_keys(item: dict) -> set[str]:
    keys = set()
    product_id = str(item.get("product_id") or "").strip()
    sku = str(item.get("sku") or "").strip().lower()
    name = str(item.get("name") or "").strip().lower()
    if product_id:
        keys.add(f"id:{product_id}")
    if sku:
        keys.add(f"sku:{sku}")
    if name:
        keys.add(f"title:{name}")
    return keys

def _product_identity_keys(products: list[dict]) -> set[str]:
    keys = set()
    for product in products:
        product_id = str(product.get("shopify_product_id") or product.get("external_id") or "").strip()
        sku_values = []
        if product.get("sku"):
            sku_values.extend(str(product["sku"]).split(","))
        for sku in product.get("skus") or []:
            sku_values.append(str(sku))
        title = str(product.get("title") or "").strip().lower()
        if product_id:
            keys.add(f"id:{product_id}")
        for sku in sku_values:
            clean_sku = sku.strip().lower()
            if clean_sku:
                keys.add(f"sku:{clean_sku}")
        if title:
            keys.add(f"title:{title}")
    return keys

def _product_lookup(products: list[dict]) -> dict[str, dict]:
    lookup = {}
    for product in products:
        for key in _product_identity_keys([product]):
            lookup.setdefault(key, product)
    return lookup

def _quantity(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0

def _order_status_text(order: dict) -> str:
    status = order.get("fulfillment_status") or order.get("status") or "received"
    parts = [f"Your order {order.get('order_number')} status is {status}."]
    if order.get("tracking_number"):
        parts.append(f"Tracking number: {order['tracking_number']}.")
    if order.get("tracking_url"):
        parts.append(f"Track here: {order['tracking_url']}")
    if order.get("total"):
        parts.append(f"Total: {order['total']} {order.get('currency') or ''}".strip())
    return " ".join(parts)

__all__ = [
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
]
