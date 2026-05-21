import json
import re

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models.ecommerce import EcommerceConnection
from app.modules.ai.core.product_search_service import product_search_text, score_search_text, search_terms
from app.modules.ai.core.sales_recommendations_service import is_sales_recommendation_request
from app.modules.ecommerce.core.ecommerce_core_service import _normalize_order, _normalize_product, fetch_orders, fetch_products
from app.shared.redis import get_redis


PRODUCT_CACHE_LIMIT = 250
ORDER_CACHE_LIMIT = 100
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*id)?[\s:#-]*#?([A-Za-z0-9-]{2,})\b", re.I)
TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
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


async def find_cached_shopify_order_status(
    db: Session,
    phone: str,
    query: str,
) -> str | None:
    order_id = _extract_order_id(query)
    cache_key = f"shopify:order-status:v1:{phone}:{order_id or 'latest'}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, str):
        return cached

    orders = await _cached_shopify_orders(db)
    if not orders:
        return None

    order = _matching_order(orders, phone, order_id)
    if not order:
        return None

    reply = _order_status_text(order)
    await _redis_set_json(cache_key, reply, settings.shopify_order_cache_ttl_seconds)
    return reply


async def find_cached_shopify_product_recommendations(
    db: Session,
    query: str,
    limit: int = 5,
) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit)
    return products


async def find_cached_shopify_catalog_products(
    db: Session,
    query: str,
    limit: int = 5,
) -> list[dict]:
    if not is_catalog_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit, allow_fallback=True)
    return products


async def find_cached_shopify_product_image(
    db: Session,
    query: str,
) -> dict | None:
    if not is_image_request(query):
        return None
    products = await _rank_cached_shopify_products(db, query, 1, require_image=True, allow_fallback=True)
    return products[0] if products else None


async def _rank_cached_shopify_products(
    db: Session,
    query: str,
    limit: int,
    require_image: bool = False,
    allow_fallback: bool = False,
) -> list[dict]:
    limit = max(1, min(limit, 10))
    query_key = _query_cache_key(query, limit, require_image, allow_fallback)
    cached = await _redis_get_json(query_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_shopify_products(db)
    if require_image:
        products = [product for product in products if product.get("image_url")]
    if not products:
        return []

    query_terms = search_terms(query)
    scored = []
    for product in products:
        score = score_search_text(query_terms, product_search_text(product))
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

    result = ranked[:limit]
    if result:
        await _redis_set_json(query_key, result, settings.shopify_query_cache_ttl_seconds)
    return result


async def _cached_shopify_products(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection:
        return []

    cache_key = f"shopify:products:v1:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    try:
        raw_products = await run_in_threadpool(fetch_products, connection, PRODUCT_CACHE_LIMIT)
    except Exception:
        return []

    products = [_product_result(_normalize_product(connection, product)) for product in raw_products]
    await _redis_set_json(cache_key, products, settings.shopify_product_cache_ttl_seconds)
    return products


async def _cached_shopify_orders(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection:
        return []

    cache_key = f"shopify:orders:v1:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    try:
        raw_orders = await run_in_threadpool(fetch_orders, connection, ORDER_CACHE_LIMIT)
    except Exception:
        return []

    orders = [_normalize_order(connection, order) for order in raw_orders]
    await _redis_set_json(cache_key, orders, settings.shopify_order_cache_ttl_seconds)
    return orders


def _active_shopify_connection(db: Session) -> EcommerceConnection | None:
    return db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.platform == "shopify", EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.desc())
    ).scalars().first()


def _extract_order_id(query: str) -> str | None:
    match = ORDER_RE.search(query or "")
    return match.group(1).upper() if match else None


def is_catalog_request(query: str) -> bool:
    terms = set(_tokens(query))
    return bool(terms & CATALOG_REQUEST_TERMS and terms & REQUEST_ACTION_TERMS)


def is_image_request(query: str) -> bool:
    return bool(set(_tokens(query)) & IMAGE_REQUEST_TERMS)


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


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


def _product_result(product: dict) -> dict:
    image_urls = product.get("image_urls") or []
    return {
        "source": "shopify_api",
        "title": product.get("title"),
        "description": product.get("description"),
        "category": product.get("product_type"),
        "brand": product.get("vendor"),
        "tags": product.get("tags"),
        "product_type": product.get("product_type"),
        "price_min": product.get("price_min"),
        "price_max": product.get("price_max"),
        "price": _price_range(product),
        "product_url": product.get("product_url"),
        "image_url": image_urls[0] if image_urls else None,
        "caption": _product_caption(product, image_urls),
        "sku": product.get("sku"),
        "external_id": product.get("external_id"),
        "shopify_product_id": product.get("shopify_product_id"),
        "retailer_id": _first_retailer_id(product),
    }


def _price_range(product: dict) -> str:
    price_min = product.get("price_min") or ""
    price_max = product.get("price_max") or ""
    if price_min and price_max and price_min != price_max:
        return f"{price_min} - {price_max}"
    return price_min or price_max or ""


def _product_caption(product: dict, image_urls: list[str]) -> str:
    parts = [str(product.get("title") or "Product")]
    price = _price_range(product)
    if price:
        parts.append(f"Price: {price}")
    if product.get("product_url"):
        parts.append(str(product["product_url"]))
    elif image_urls:
        parts.append(image_urls[0])
    return "\n".join(parts)


def _first_retailer_id(product: dict) -> str | None:
    skus = []
    if product.get("sku"):
        skus.extend(part.strip() for part in str(product["sku"]).split(",") if part.strip())
    for sku in product.get("skus") or []:
        if isinstance(sku, str) and sku.strip():
            skus.append(sku.strip())
    if skus:
        return sorted(set(skus))[0]
    return product.get("external_id") or product.get("shopify_product_id")


def _query_cache_key(query: str, limit: int, require_image: bool, allow_fallback: bool) -> str:
    normalized = " ".join((query or "").lower().split())[:180]
    flags = f"limit:{limit}:image:{int(require_image)}:fallback:{int(allow_fallback)}"
    return f"shopify:query:v1:{flags}:{normalized}"


async def _redis_get_json(key: str):
    try:
        redis = await get_redis()
        value = await redis.get(key)
    except RedisError:
        return None
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


async def _redis_set_json(key: str, value, ttl_seconds: int) -> None:
    try:
        redis = await get_redis()
        await redis.setex(key, max(1, ttl_seconds), json.dumps(value, ensure_ascii=True))
    except RedisError:
        return
