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
    ShopifyCatalogDefaultCategory,
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
from app.modules.ecommerce.catalog.shopify_catalog_text_runtime import (
    _category_slug,
    _is_clean_category_label,
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
DEFAULT_CATALOG_CATEGORY_ROWS = [
    {
        "category_key": "all",
        "id": "catalog:all",
        "title": "All products",
        "description": "Browse the full catalog",
        "sort_order": 0,
    },
    {
        "category_key": "best_sellers",
        "id": "catalog:best_sellers",
        "title": "Best sellers",
        "description": "Popular products",
        "sort_order": 1,
    },
]


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
        "in_stock": product.get("in_stock"),
        "stock_quantity": product.get("stock_quantity"),
        "availability_label": product.get("availability_label"),
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
    if product.get("availability_label"):
        parts.append(str(product["availability_label"]))
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


async def _cached_shopify_products(db: Session, phone: str | None = None) -> list[dict]:
    return await _cached_visible_catalog_products(db, phone=phone)

async def _cached_all_shopify_products(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    cache_key = f"shopify:products:all:v2:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    try:
        raw_products = await run_in_threadpool(fetch_all_products, connection, PRODUCT_CATALOG_CACHE_LIMIT)
    except Exception:
        return []

    products = [_product_result(_normalize_product(connection, product)) for product in raw_products]
    await _redis_set_json(cache_key, products, settings.SHOPIFY_PRODUCT_CACHE_TTL_SECONDS)
    return products

async def _cached_visible_catalog_products(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    selected_collections = _selected_shopify_collections(db, connection.id)
    products = await _cached_all_shopify_products(db, phone=phone)
    if selected_collections is None:
        return []

    index = await _shopify_collection_index(db, phone=phone)
    allowed_ids = {
        str(product_id)
        for collection in index
        for product_id in collection.get("product_ids", [])
    }
    if not allowed_ids:
        return []
    return [
        product
        for product in products
        if str(product.get("shopify_product_id") or product.get("external_id") or "") in allowed_ids
    ]

async def _cached_shopify_collection_categories(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection or connection.platform != "shopify":
        return []

    cache_key = f"shopify:collections:v4:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list) and cached:
        return cached

    saved_categories = _saved_shopify_collection_category_rows(db, connection.id)
    if saved_categories:
        await _redis_set_json(cache_key, saved_categories, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
        return saved_categories

    index = await _shopify_collection_index(db, phone=phone)
    categories = [
        {
            "id": f"catalog:category:collection_{item['slug']}",
            "title": item["title"][:24],
            "description": f"{len(item['product_ids'])} products",
            "sort_order": item.get("sort_order", 0),
        }
        for item in index
        if item.get("product_ids")
    ]
    if categories:
        await _redis_set_json(cache_key, categories, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    return categories

async def _cached_shopify_collection_products(
    db: Session,
    category_key: str,
    phone: str | None = None,
) -> list[dict]:
    if not category_key.startswith("collection_"):
        return []
    slug = category_key.removeprefix("collection_")
    index = await _shopify_collection_index(db, phone=phone)
    collection = next((item for item in index if item.get("slug") == slug), None)
    if not collection:
        return []

    products = await _cached_all_shopify_products(db, phone=phone)
    product_by_id = {
        str(product.get("shopify_product_id") or product.get("external_id") or ""): product
        for product in products
    }
    return [
        product_by_id[product_id]
        for product_id in collection.get("product_ids", [])
        if product_id in product_by_id
    ]

async def _shopify_collection_index(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection or connection.platform != "shopify":
        return []

    cache_key = f"shopify:collection-index:v4:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list) and cached:
        return cached

    try:
        collections, collects, raw_products = await run_in_threadpool(_fetch_shopify_collection_payload, connection)
    except Exception:
        return []

    in_stock_product_ids = {
        str(normalized.get("shopify_product_id") or normalized.get("external_id") or "")
        for normalized in (_normalize_product(connection, product) for product in raw_products)
        if normalized.get("status") == "active" and _has_sellable_stock(normalized)
    }
    products_by_collection: dict[str, list[str]] = defaultdict(list)
    for collect in collects:
        collection_id = str(collect.get("collection_id") or "")
        product_id = str(collect.get("product_id") or "")
        if collection_id and product_id and product_id in in_stock_product_ids:
            products_by_collection[collection_id].append(product_id)

    selected_collections = _selected_shopify_collections(db, connection.id)
    index = []
    for collection in collections:
        collection_id = str(collection.get("id") or "")
        if selected_collections is not None and collection_id not in selected_collections:
            continue
        title = str(collection.get("title") or "").strip()
        slug = _category_slug(collection.get("handle") or title)
        product_ids = products_by_collection.get(collection_id, [])
        if title and slug and product_ids and _is_clean_category_label(title, set()):
            index.append(
                {
                    "collection_id": collection_id,
                    "slug": slug,
                    "title": title,
                    "product_ids": product_ids,
                    "sort_order": selected_collections.get(collection_id, 0) if selected_collections else 0,
                }
            )

    if selected_collections is not None:
        index.sort(key=lambda item: (int(item.get("sort_order") or 0), item["title"].lower()))
    else:
        index.sort(key=lambda item: len(item["product_ids"]), reverse=True)
    await _redis_set_json(cache_key, index, settings.SHOPIFY_QUERY_CACHE_TTL_SECONDS)
    return index

def _fetch_shopify_collection_payload(connection: EcommerceConnection) -> tuple[list[dict], list[dict], list[dict]]:
    return (
        fetch_shopify_collections(connection, 250),
        fetch_shopify_collects(connection, PRODUCT_CATALOG_CACHE_LIMIT),
        fetch_all_products(connection, PRODUCT_CATALOG_CACHE_LIMIT),
    )

def _has_sellable_stock(product: dict) -> bool:
    stock_quantity = product.get("stock_quantity")
    if isinstance(stock_quantity, int):
        return stock_quantity > 0
    return bool(product.get("in_stock"))

def _selected_shopify_collections(db: Session, connection_id: int) -> dict[str, int] | None:
    rows = db.execute(
        select(ShopifyCatalogCollection).where(
            ShopifyCatalogCollection.connection_id == connection_id
        )
    ).scalars().all()
    if not rows:
        return None
    return {
        str(row.shopify_collection_id): int(row.sort_order or 0)
        for row in rows
        if str(row.visible or "").strip().lower() in {"1", "true", "yes", "on"}
    }

def _saved_shopify_collection_category_rows(db: Session, connection_id: int) -> list[dict]:
    rows = db.execute(
        select(ShopifyCatalogCollection).where(
            ShopifyCatalogCollection.connection_id == connection_id
        )
    ).scalars().all()
    categories = []
    for row in rows:
        if str(row.visible or "").strip().lower() not in {"1", "true", "yes", "on"}:
            continue
        title = str(row.title or "").strip()
        slug = _category_slug(row.handle or title)
        if not title or not slug or not _is_saved_collection_label(title):
            continue
        product_count = int(row.product_count or 0)
        categories.append(
            {
                "id": f"catalog:category:collection_{slug}",
                "title": title[:24],
                "description": f"{product_count} products",
                "sort_order": int(row.sort_order or 0),
            }
        )
    return sorted(categories, key=lambda item: (int(item.get("sort_order") or 0), item["title"].lower()))

def _is_saved_collection_label(label: str) -> bool:
    normalized = " ".join((label or "").lower().split())
    if len(normalized) < 2:
        return False
    if NON_CATEGORY_LABEL_RE.match(normalized):
        return False
    if re.search(r"\b(?:gst|igst|cgst|sgst|vat|tax|taxable)\b", normalized):
        return False
    return True

def selected_default_catalog_category_rows(db: Session, connection_id: int) -> list[dict] | None:
    rows = db.execute(
        select(ShopifyCatalogDefaultCategory).where(
            ShopifyCatalogDefaultCategory.connection_id == connection_id
        )
    ).scalars().all()
    if not rows:
        return None

    default_by_key = {row["category_key"]: row for row in DEFAULT_CATALOG_CATEGORY_ROWS}
    selected = []
    for row in rows:
        default = default_by_key.get(str(row.category_key))
        if not default:
            continue
        if str(row.visible or "").strip().lower() not in {"1", "true", "yes", "on"}:
            continue
        selected.append(
            {
                "id": default["id"],
                "title": default["title"],
                "description": default["description"],
                "sort_order": int(row.sort_order or 0),
            }
        )
    return sorted(selected, key=lambda item: (int(item.get("sort_order") or 0), item["title"].lower()))

async def _cached_shopify_orders(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
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
    await _redis_set_json(cache_key, orders, settings.SHOPIFY_ORDER_CACHE_TTL_SECONDS)
    return orders

async def _cached_shopify_sales_orders(db: Session, phone: str | None = None) -> list[dict]:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection:
        return []

    cache_key = f"shopify:orders:sales:v1:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    try:
        raw_orders = await run_in_threadpool(fetch_orders_for_sales, connection, TOP_SELLING_ORDER_LIMIT)
    except Exception:
        return []

    orders = [_normalize_order(connection, order) for order in raw_orders]
    await _redis_set_json(cache_key, orders, settings.SHOPIFY_ORDER_CACHE_TTL_SECONDS)
    return orders

async def _cached_shopify_order_by_id(
    db: Session,
    order_id: str | None,
    phone: str | None = None,
) -> dict | None:
    connection = _active_shopify_connection(db, phone=phone)
    if not connection or not order_id:
        return None

    normalized_order_id = order_id.lstrip("#").upper()
    cache_key = f"shopify:order:v1:{connection.id}:{normalized_order_id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        raw_order = await run_in_threadpool(fetch_order_by_number, connection, normalized_order_id)
    except Exception:
        return None
    if not raw_order:
        return None

    order = _normalize_order(connection, raw_order)
    await _redis_set_json(cache_key, order, settings.SHOPIFY_ORDER_CACHE_TTL_SECONDS)
    return order

def _query_cache_key(
    connection_id: int,
    query: str,
    limit: int,
    require_image: bool,
    allow_fallback: bool,
    entities: dict | None = None,
) -> str:
    normalized = " ".join((query or "").lower().split())[:180]
    entity_part = ""
    if isinstance(entities, dict) and entities:
        entity_part = json.dumps(entities, sort_keys=True, ensure_ascii=True)[:240]
    flags = f"limit:{limit}:image:{int(require_image)}:fallback:{int(allow_fallback)}"
    return f"shopify:query:v3:{connection_id}:{flags}:{normalized}:{entity_part}"

def _local_cache_get(key: str):
    cached = _local_query_cache.get(key)
    if not cached:
        return None
    cached_at, value = cached
    if time.monotonic() - cached_at > LOCAL_QUERY_CACHE_TTL_SECONDS:
        _local_query_cache.pop(key, None)
        return None
    return value

def _local_cache_set(key: str, value) -> None:
    if len(_local_query_cache) > 500:
        _local_query_cache.clear()
    _local_query_cache[key] = (time.monotonic(), value)

async def _redis_get_json(key: str):
    try:
        redis = await get_redis()
        value = await redis.get(key)
    except (RedisError, RuntimeError, OSError):
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
    except (RedisError, RuntimeError, OSError):
        return

__all__ = [
    "_cached_shopify_products",
    "_cached_all_shopify_products",
    "_cached_shopify_collection_categories",
    "_cached_shopify_collection_products",
    "_shopify_collection_index",
    "_fetch_shopify_collection_payload",
    "_selected_shopify_collections",
    "_saved_shopify_collection_category_rows",
    "_is_saved_collection_label",
    "selected_default_catalog_category_rows",
    "_cached_shopify_orders",
    "_cached_shopify_sales_orders",
    "_cached_shopify_order_by_id",
    "_product_result",
    "_price_range",
    "_product_caption",
    "_first_retailer_id",
    "_query_cache_key",
    "_local_cache_get",
    "_local_cache_set",
    "_redis_get_json",
    "_redis_set_json",
]
