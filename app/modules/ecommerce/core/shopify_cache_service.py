import json
import re
from collections import Counter, defaultdict

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.models.ecommerce import EcommerceConnection, ShopifyCatalogCollection
from app.modules.ai.core.product_search_service import product_search_text, score_search_text, search_terms
from app.modules.ai.core.sales_recommendations_service import is_sales_recommendation_request
from app.modules.ecommerce.core.ecommerce_core_service import (
    _normalize_order,
    _normalize_product,
    fetch_all_products,
    fetch_order_by_number,
    fetch_orders,
    fetch_orders_for_sales,
    fetch_shopify_collections,
    fetch_shopify_collects,
)
from app.shared.redis import get_redis


PRODUCT_CATALOG_CACHE_LIMIT = 5000
ORDER_CACHE_LIMIT = 100
TOP_SELLING_ORDER_LIMIT = 500
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

    order = await _cached_shopify_order_by_id(db, order_id) if order_id else None
    if not order:
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
    entities: dict | None = None,
) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit, entities=entities)
    return products


async def find_cached_shopify_cross_sell_products(
    db: Session,
    query: str,
    base_products: list[dict],
    limit: int = 3,
) -> list[dict]:
    limit = max(1, min(limit, 5))
    if not base_products:
        return []

    signature = ",".join(
        sorted(
            str(product.get("external_id") or product.get("shopify_product_id") or product.get("sku") or product.get("title") or "")
            for product in base_products
        )
    )[:160]
    cache_key = f"shopify:cross-sell:v2:limit:{limit}:{signature}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_all_shopify_products(db)
    if not products:
        return []

    result = await _frequently_bought_together_products(db, base_products, products, limit)
    if result:
        await _redis_set_json(cache_key, result, settings.shopify_query_cache_ttl_seconds)
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
        await _redis_set_json(cache_key, result, settings.shopify_query_cache_ttl_seconds)
    return result


async def find_cached_shopify_top_selling_products(
    db: Session,
    limit: int = 3,
) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection:
        return []

    limit = max(1, min(limit, 10))
    cache_key = f"shopify:top-selling:v1:{connection.id}:limit:{limit}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    orders = await _cached_shopify_sales_orders(db)
    if not orders:
        return []

    products = await _cached_all_shopify_products(db)
    ranked = _top_selling_from_orders(orders, products, limit)
    if ranked:
        await _redis_set_json(cache_key, ranked, settings.shopify_query_cache_ttl_seconds)
    return ranked


async def find_cached_shopify_catalog_products(
    db: Session,
    query: str,
    limit: int = 5,
    entities: dict | None = None,
) -> list[dict]:
    if not is_catalog_request(query):
        return []
    products = await _rank_cached_shopify_products(db, query, limit, allow_fallback=True, entities=entities)
    return products


async def find_cached_shopify_category_products(
    db: Session,
    category: str,
    limit: int = 5,
    offset: int = 0,
) -> list[dict]:
    limit = max(1, min(limit, 10))
    offset = max(0, offset)
    category_key = " ".join((category or "").lower().split())[:80]
    if not category_key:
        return []

    cache_key = f"shopify:category:v2:{category_key}:limit:{limit}:offset:{offset}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_all_shopify_products(db)
    if not products:
        return []

    collection_products = await _cached_shopify_collection_products(db, category_key)
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
        await _redis_set_json(cache_key, result, settings.shopify_query_cache_ttl_seconds)
    return result


async def find_cached_shopify_catalog_categories(
    db: Session,
    limit: int = 24,
) -> list[dict]:
    limit = max(1, min(limit, 50))
    cache_key = f"shopify:categories:v5:limit:{limit}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_all_shopify_products(db)
    if not products:
        return []

    collection_categories = await _cached_shopify_collection_categories(db)
    if collection_categories:
        categories = collection_categories[:limit]
        await _redis_set_json(cache_key, categories, settings.shopify_query_cache_ttl_seconds)
        return categories

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
    await _redis_set_json(cache_key, categories, settings.shopify_query_cache_ttl_seconds)
    return categories


async def find_cached_shopify_product_image(
    db: Session,
    query: str,
    entities: dict | None = None,
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
    )
    return products[0] if products else None


async def _rank_cached_shopify_products(
    db: Session,
    query: str,
    limit: int,
    require_image: bool = False,
    allow_fallback: bool = False,
    entities: dict | None = None,
) -> list[dict]:
    limit = max(1, min(limit, 10))
    query_key = _query_cache_key(query, limit, require_image, allow_fallback, entities)
    cached = await _redis_get_json(query_key)
    if isinstance(cached, list):
        return cached

    products = await _cached_shopify_products(db)
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
        )

    result = ranked[:limit]
    if result:
        await _redis_set_json(query_key, result, settings.shopify_query_cache_ttl_seconds)
    return result


async def _cached_shopify_products(db: Session) -> list[dict]:
    return await _cached_all_shopify_products(db)


async def _cached_all_shopify_products(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
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
    await _redis_set_json(cache_key, products, settings.shopify_product_cache_ttl_seconds)
    return products


async def _cached_shopify_collection_categories(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection or connection.platform != "shopify":
        return []

    cache_key = f"shopify:collections:v2:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    index = await _shopify_collection_index(db)
    categories = [
        {
            "id": f"catalog:category:collection_{item['slug']}",
            "title": item["title"][:24],
            "description": f"{len(item['product_ids'])} products",
        }
        for item in index
        if item.get("product_ids")
    ]
    await _redis_set_json(cache_key, categories, settings.shopify_query_cache_ttl_seconds)
    return categories


async def _cached_shopify_collection_products(db: Session, category_key: str) -> list[dict]:
    if not category_key.startswith("collection_"):
        return []
    slug = category_key.removeprefix("collection_")
    index = await _shopify_collection_index(db)
    collection = next((item for item in index if item.get("slug") == slug), None)
    if not collection:
        return []

    products = await _cached_all_shopify_products(db)
    product_by_id = {
        str(product.get("shopify_product_id") or product.get("external_id") or ""): product
        for product in products
    }
    return [
        product_by_id[product_id]
        for product_id in collection.get("product_ids", [])
        if product_id in product_by_id
    ]


async def _shopify_collection_index(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection or connection.platform != "shopify":
        return []

    cache_key = f"shopify:collection-index:v2:{connection.id}"
    cached = await _redis_get_json(cache_key)
    if isinstance(cached, list):
        return cached

    try:
        collections, collects = await run_in_threadpool(_fetch_shopify_collection_payload, connection)
    except Exception:
        return []

    products_by_collection: dict[str, list[str]] = defaultdict(list)
    for collect in collects:
        collection_id = str(collect.get("collection_id") or "")
        product_id = str(collect.get("product_id") or "")
        if collection_id and product_id:
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
    await _redis_set_json(cache_key, index, settings.shopify_query_cache_ttl_seconds)
    return index


def _fetch_shopify_collection_payload(connection: EcommerceConnection) -> tuple[list[dict], list[dict]]:
    return fetch_shopify_collections(connection, 250), fetch_shopify_collects(connection, PRODUCT_CATALOG_CACHE_LIMIT)


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


async def _cached_shopify_sales_orders(db: Session) -> list[dict]:
    connection = _active_shopify_connection(db)
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
    await _redis_set_json(cache_key, orders, settings.shopify_order_cache_ttl_seconds)
    return orders


async def _cached_shopify_order_by_id(db: Session, order_id: str | None) -> dict | None:
    connection = _active_shopify_connection(db)
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
    await _redis_set_json(cache_key, order, settings.shopify_order_cache_ttl_seconds)
    return order


def _active_shopify_connection(db: Session) -> EcommerceConnection | None:
    return db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.platform == "shopify", EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.desc())
    ).scalars().first()


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
) -> list[dict]:
    connection = _active_shopify_connection(db)
    if not connection:
        return []

    cache_key = f"shopify:fbt:v1:{connection.id}"
    index = await _redis_get_json(cache_key)
    if not isinstance(index, dict):
        orders = await _cached_shopify_sales_orders(db)
        index = _frequently_bought_together_index(orders)
        if index:
            await _redis_set_json(cache_key, index, settings.shopify_query_cache_ttl_seconds)
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


def _query_cache_key(query: str, limit: int, require_image: bool, allow_fallback: bool, entities: dict | None = None) -> str:
    normalized = " ".join((query or "").lower().split())[:180]
    entity_part = ""
    if isinstance(entities, dict) and entities:
        entity_part = json.dumps(entities, sort_keys=True, ensure_ascii=True)[:240]
    flags = f"limit:{limit}:image:{int(require_image)}:fallback:{int(allow_fallback)}"
    return f"shopify:query:v2:{flags}:{normalized}:{entity_part}"


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
