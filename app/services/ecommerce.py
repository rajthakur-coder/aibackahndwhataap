import json
import base64
import hashlib
import hmac
import time
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.utils import parse_header_links
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ecommerce import EcommerceConnection, EcommerceOrder, EcommerceProduct
from app.models.entities import AgentAction, EcommerceCustomer, ShopifyWebhookEvent
try:
    from cryptography.fernet import Fernet
except ImportError:  # pragma: no cover - dependency is declared for production installs.
    Fernet = None

REQUEST_TIMEOUT = 30
SHOPIFY_API_VERSION = "2025-04"
SUPPORTED_PLATFORMS = {"shopify", "woocommerce"}
DELIVERED_STATUSES = {"delivered"}
SHOPIFY_WEBHOOK_TOPICS = {
    "orders/create": "/webhooks/shopify/orders",
    "orders/updated": "/webhooks/shopify/orders",
    "fulfillments/create": "/webhooks/shopify/fulfillments",
    "fulfillments/update": "/webhooks/shopify/fulfillments",
    "products/create": "/webhooks/shopify/products",
    "products/update": "/webhooks/shopify/products",
}


def _clean_platform(platform: str) -> str:
    value = platform.strip().lower()
    if value not in SUPPORTED_PLATFORMS:
        raise ValueError("Platform must be shopify or woocommerce")
    return value


def _normalize_store_url(store_url: str, platform: str) -> str:
    value = store_url.strip().rstrip("/")
    if not value:
        raise ValueError("Store URL is required")

    if platform == "shopify":
        value = value.replace("https://", "").replace("http://", "").strip("/")
        return value

    if not urlparse(value).scheme:
        value = f"https://{value}"
    return value


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True)


def _encrypt_token(token: str | None) -> str | None:
    if not token:
        return None
    secret = settings.ecommerce_token_secret
    if not secret or Fernet is None:
        return token
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
    return "fernet:" + Fernet(key).encrypt(token.encode()).decode()


def _decrypt_token(token: str | None) -> str | None:
    if not token:
        return None
    if token.startswith("fernet:"):
        secret = settings.ecommerce_token_secret
        if not secret or Fernet is None:
            return token
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key).decrypt(token[7:].encode()).decode()
    if not token.startswith("xor:"):
        return token
    secret = settings.ecommerce_token_secret
    if not secret:
        return token
    key = hashlib.sha256(secret.encode()).digest()
    data = base64.urlsafe_b64decode(token[4:].encode())
    return bytes(char ^ key[index % len(key)] for index, char in enumerate(data)).decode()


def create_connection(
    db: Session,
    name: str,
    platform: str,
    store_url: str,
    access_token: str | None = None,
    consumer_key: str | None = None,
    consumer_secret: str | None = None,
) -> EcommerceConnection:
    platform = _clean_platform(platform)
    store_url = _normalize_store_url(store_url, platform)

    if platform == "shopify" and not access_token:
        raise ValueError("Shopify access token is required")
    if platform == "woocommerce" and (not consumer_key or not consumer_secret):
        raise ValueError("WooCommerce consumer key and consumer secret are required")

    existing = (
        db.query(EcommerceConnection)
        .filter(
            EcommerceConnection.platform == platform,
            EcommerceConnection.store_url == store_url,
        )
        .first()
    )
    if existing:
        existing.name = name.strip() or existing.name or store_url
        existing.status = "active"
        if platform == "shopify":
            existing.myshopify_domain = existing.myshopify_domain or store_url
        if access_token:
            existing.access_token = access_token
            existing.encrypted_access_token = _encrypt_token(access_token)
        if consumer_key:
            existing.consumer_key = consumer_key
        if consumer_secret:
            existing.consumer_secret = consumer_secret
        db.commit()
        db.refresh(existing)
        if platform == "shopify":
            bootstrap_shopify_connection(db, existing)
        return existing

    connection = EcommerceConnection(
        name=name.strip() or store_url,
        platform=platform,
        store_url=store_url,
        myshopify_domain=store_url if platform == "shopify" else None,
        access_token=access_token,
        encrypted_access_token=_encrypt_token(access_token),
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    if platform == "shopify":
        bootstrap_shopify_connection(db, connection)
    return connection


def update_connection(
    db: Session,
    connection: EcommerceConnection,
    name: str | None = None,
    store_url: str | None = None,
    access_token: str | None = None,
    consumer_key: str | None = None,
    consumer_secret: str | None = None,
    status: str | None = None,
) -> EcommerceConnection:
    if name is not None:
        connection.name = name.strip() or connection.name
    if store_url is not None:
        connection.store_url = _normalize_store_url(store_url, connection.platform)
    if access_token:
        connection.access_token = access_token
        connection.encrypted_access_token = _encrypt_token(access_token)
    if consumer_key:
        connection.consumer_key = consumer_key
    if consumer_secret:
        connection.consumer_secret = consumer_secret
    if status is not None:
        connection.status = status

    db.commit()
    db.refresh(connection)
    return connection


def _shopify_headers(connection: EcommerceConnection) -> dict:
    return {
        "X-Shopify-Access-Token": _decrypt_token(connection.encrypted_access_token) or connection.access_token or "",
        "Content-Type": "application/json",
    }


def _shopify_base_url(connection: EcommerceConnection) -> str:
    domain = connection.myshopify_domain or connection.store_url
    return f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}"


def _shopify_request(
    method: str,
    connection: EcommerceConnection,
    path: str,
    params: dict | None = None,
    payload: dict | None = None,
) -> requests.Response:
    url = f"{_shopify_base_url(connection)}{path}"
    for attempt in range(4):
        response = requests.request(
            method,
            url,
            headers=_shopify_headers(connection),
            params=params,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 429:
            response.raise_for_status()
            return response
        retry_after = response.headers.get("Retry-After")
        sleep_for = float(retry_after) if retry_after else min(2 ** attempt, 8)
        time.sleep(sleep_for)
    response.raise_for_status()
    return response


def _next_page_info(response: requests.Response) -> str | None:
    link_header = response.headers.get("Link")
    if not link_header:
        return None
    for link in parse_header_links(link_header.rstrip(">").replace(">,", ",")):
        if link.get("rel") != "next":
            continue
        query = urlparse(link.get("url", "")).query
        for part in query.split("&"):
            key, _, value = part.partition("=")
            if key == "page_info":
                return value
    return None


def _woocommerce_base_url(connection: EcommerceConnection) -> str:
    return f"{connection.store_url}/wp-json/wc/v3"


def test_connection(connection: EcommerceConnection) -> dict:
    if connection.platform == "shopify":
        response = _shopify_request("GET", connection, "/shop.json")
        shop = response.json().get("shop", {})
        connection.store_name = shop.get("name") or connection.store_name
        connection.myshopify_domain = shop.get("myshopify_domain") or connection.myshopify_domain or connection.store_url
        connection.shopify_shop_id = str(shop.get("id") or connection.shopify_shop_id or "")
        connection.currency = shop.get("currency") or connection.currency
        connection.timezone = shop.get("iana_timezone") or shop.get("timezone") or connection.timezone
        connection.owner_email = shop.get("email") or connection.owner_email
        connection.owner_phone = shop.get("phone") or connection.owner_phone
        connection.plan_name = shop.get("plan_name") or connection.plan_name
        connection.status = "active"
        db_session = getattr(connection, "_sa_instance_state", None)
        if db_session:
            pass
        return {"ok": True, "platform": "shopify", "store": shop.get("name") or connection.store_url}

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/system_status",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return {"ok": True, "platform": "woocommerce", "store": data.get("environment", {}).get("site_url") or connection.store_url}


def fetch_orders(connection: EcommerceConnection, limit: int = 50) -> list[dict]:
    limit = max(1, min(limit, 100))
    if connection.platform == "shopify":
        orders = []
        page_info = None
        while len(orders) < limit:
            params = {
                "status": "any",
                "limit": min(250, limit - len(orders)),
                "fields": "id,name,email,phone,tags,note,subtotal_price,total_price,total_discounts,total_tax,currency,financial_status,fulfillment_status,line_items,shipping_address,billing_address,customer,fulfillments,payment_gateway_names,created_at,updated_at",
            }
            if page_info:
                params = {"limit": min(250, limit - len(orders)), "page_info": page_info}
            response = _shopify_request("GET", connection, "/orders.json", params=params)
            orders.extend(response.json().get("orders", []))
            page_info = _next_page_info(response)
            if not page_info:
                break
        return orders[:limit]

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/orders",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        params={"per_page": limit, "orderby": "date", "order": "desc"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_products(connection: EcommerceConnection, limit: int = 100) -> list[dict]:
    limit = max(1, min(limit, 250))
    if connection.platform == "shopify":
        products = []
        page_info = None
        while len(products) < limit:
            params = {
                "limit": limit,
                "fields": "id,title,handle,body_html,vendor,product_type,tags,status,variants,images,options,created_at,updated_at",
            }
            if page_info:
                params = {"limit": min(250, limit - len(products)), "page_info": page_info}
            response = _shopify_request("GET", connection, "/products.json", params=params)
            products.extend(response.json().get("products", []))
            page_info = _next_page_info(response)
            if not page_info:
                break
        return products[:limit]

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/products",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        params={"per_page": min(limit, 100), "orderby": "date", "order": "desc"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_customers(connection: EcommerceConnection, limit: int = 100) -> list[dict]:
    limit = max(1, min(limit, 250))
    if connection.platform != "shopify":
        return []
    customers = []
    page_info = None
    while len(customers) < limit:
        params = {
            "limit": min(250, limit - len(customers)),
            "fields": "id,email,phone,first_name,last_name,orders_count,total_spent,tags,addresses,default_address,email_marketing_consent,locale,created_at,updated_at",
        }
        if page_info:
            params = {"limit": min(250, limit - len(customers)), "page_info": page_info}
        response = _shopify_request("GET", connection, "/customers.json", params=params)
        customers.extend(response.json().get("customers", []))
        page_info = _next_page_info(response)
        if not page_info:
            break
    return customers[:limit]


def _plain_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())


def _price_range(values: list[str | int | float | None]) -> tuple[str | None, str | None]:
    prices = []
    for value in values:
        if value in {None, ""}:
            continue
        try:
            prices.append(float(value))
        except (TypeError, ValueError):
            continue
    if not prices:
        return None, None
    low = min(prices)
    high = max(prices)
    return f"{low:g}", f"{high:g}"


def _normalize_shopify_product(connection: EcommerceConnection, product: dict) -> dict:
    variants = product.get("variants") or []
    images = product.get("images") or []
    price_min, price_max = _price_range([variant.get("price") for variant in variants])
    skus = [variant.get("sku") for variant in variants if variant.get("sku")]
    inventory_values = [
        str(variant.get("inventory_quantity"))
        for variant in variants
        if variant.get("inventory_quantity") is not None
    ]
    handle = product.get("handle")
    return {
        "external_id": str(product.get("id")),
        "shopify_product_id": str(product.get("id")),
        "title": product.get("title") or "Untitled product",
        "handle": handle,
        "product_url": f"https://{connection.store_url}/products/{handle}" if handle else None,
        "description_html": product.get("body_html"),
        "description": _plain_text(product.get("body_html")),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": product.get("tags"),
        "collections": [],
        "status": product.get("status"),
        "price_min": price_min,
        "price_max": price_max,
        "prices": [variant.get("price") for variant in variants if variant.get("price")],
        "compare_at_prices": [
            variant.get("compare_at_price") for variant in variants if variant.get("compare_at_price")
        ],
        "currency": None,
        "sku": ", ".join(skus[:10]) if skus else None,
        "skus": skus,
        "inventory": ", ".join(inventory_values[:20]) if inventory_values else None,
        "variants": variants,
        "options": product.get("options") or [],
        "seo_title": product.get("seo_title") or product.get("title"),
        "seo_description": product.get("seo_description"),
        "image_urls": [image.get("src") for image in images if image.get("src")],
    }


def _normalize_woocommerce_product(connection: EcommerceConnection, product: dict) -> dict:
    images = product.get("images") or []
    variations = product.get("variations") or []
    price_min, price_max = _price_range(
        [
            product.get("price"),
            product.get("regular_price"),
            product.get("sale_price"),
            product.get("min_price"),
            product.get("max_price"),
        ]
    )
    tags = ", ".join(tag.get("name", "") for tag in product.get("tags", []) if tag.get("name"))
    categories = ", ".join(category.get("name", "") for category in product.get("categories", []) if category.get("name"))
    return {
        "external_id": str(product.get("id")),
        "shopify_product_id": None,
        "title": product.get("name") or "Untitled product",
        "handle": product.get("slug"),
        "product_url": product.get("permalink"),
        "description_html": product.get("description") or product.get("short_description"),
        "description": _plain_text(product.get("description") or product.get("short_description")),
        "vendor": None,
        "product_type": categories or product.get("type"),
        "tags": tags,
        "collections": product.get("categories") or [],
        "status": product.get("status"),
        "price_min": price_min,
        "price_max": price_max,
        "prices": [product.get("price")],
        "compare_at_prices": [product.get("regular_price")],
        "currency": None,
        "sku": product.get("sku"),
        "skus": [product.get("sku")] if product.get("sku") else [],
        "inventory": str(product.get("stock_quantity")) if product.get("stock_quantity") is not None else product.get("stock_status"),
        "variants": variations[:20],
        "options": product.get("attributes") or [],
        "seo_title": product.get("name"),
        "seo_description": None,
        "image_urls": [image.get("src") for image in images if image.get("src")],
        "variation_ids": variations[:20],
    }


def _normalize_product(connection: EcommerceConnection, product: dict) -> dict:
    if connection.platform == "shopify":
        return _normalize_shopify_product(connection, product)
    return _normalize_woocommerce_product(connection, product)


def upsert_product(db: Session, connection: EcommerceConnection, product: dict) -> EcommerceProduct:
    normalized = _normalize_product(connection, product)
    row = (
        db.query(EcommerceProduct)
        .filter(
            EcommerceProduct.connection_id == connection.id,
            EcommerceProduct.external_id == normalized["external_id"],
        )
        .first()
    )
    if not row:
        row = EcommerceProduct(
            tenant_id=connection.tenant_id,
            connection_id=connection.id,
            platform=connection.platform,
            external_id=normalized["external_id"],
            title=normalized["title"],
        )
        db.add(row)

    row.tenant_id = connection.tenant_id
    row.shopify_product_id = normalized["shopify_product_id"]
    row.title = normalized["title"]
    row.handle = normalized["handle"]
    row.product_url = normalized["product_url"]
    row.description_html = normalized["description_html"]
    row.description = normalized["description"]
    row.vendor = normalized["vendor"]
    row.product_type = normalized["product_type"]
    row.tags = normalized["tags"]
    row.collections = _json_dumps(normalized["collections"])
    row.status = normalized["status"]
    row.price_min = normalized["price_min"]
    row.price_max = normalized["price_max"]
    row.prices = _json_dumps(normalized["prices"])
    row.compare_at_prices = _json_dumps(normalized["compare_at_prices"])
    row.currency = normalized["currency"]
    row.sku = normalized["sku"]
    row.skus = _json_dumps(normalized["skus"])
    row.inventory = normalized["inventory"]
    row.variants = _json_dumps(normalized["variants"])
    row.options = _json_dumps(normalized["options"])
    row.seo_title = normalized["seo_title"]
    row.seo_description = normalized["seo_description"]
    row.image_urls = _json_dumps(normalized["image_urls"])
    row.raw_payload = _json_dumps(product)
    db.commit()
    db.refresh(row)
    return row


def product_knowledge_text(product: EcommerceProduct) -> str:
    image_urls = []
    if product.image_urls:
        try:
            image_urls = json.loads(product.image_urls)
        except json.JSONDecodeError:
            image_urls = []

    price = product.price_min or ""
    if product.price_max and product.price_max != product.price_min:
        price = f"{product.price_min or ''} - {product.price_max}"

    parts = [
        f"Product: {product.title}",
        f"Platform: {product.platform}",
        f"Product URL: {product.product_url or ''}",
        f"Price: {price} {product.currency or ''}".strip(),
        f"SKU: {product.sku or ''}",
        f"Availability/Inventory: {product.inventory or ''}",
        f"Vendor: {product.vendor or ''}",
        f"Category/type: {product.product_type or ''}",
        f"Tags: {product.tags or ''}",
        f"Status: {product.status or ''}",
        "Images: " + ", ".join(image_urls[:20]),
        f"Description:\n{product.description or ''}",
    ]
    return "\n".join(part for part in parts if part.strip())


def sync_products(db: Session, connection: EcommerceConnection, limit: int = 100) -> dict:
    products = fetch_products(connection, limit=limit)
    synced = [upsert_product(db, connection, product) for product in products]
    connection.last_sync_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "fetched": len(products), "synced": len(synced)}


def sync_customers(db: Session, connection: EcommerceConnection, limit: int = 100) -> dict:
    customers = fetch_customers(connection, limit=limit)
    synced = [upsert_customer(db, connection, customer) for customer in customers]
    connection.last_sync_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "fetched": len(customers), "synced": len([row for row in synced if row])}


def register_shopify_webhooks(db: Session, connection: EcommerceConnection) -> dict:
    if connection.platform != "shopify":
        return {"status": "skipped", "reason": "not_shopify"}
    base_url = settings.public_webhook_base_url
    if not base_url:
        connection.webhook_status = "missing_public_url"
        db.commit()
        return {"status": "skipped", "reason": "PUBLIC_WEBHOOK_BASE_URL or APP_URL is required"}

    registered = 0
    failed = []
    for topic, path in SHOPIFY_WEBHOOK_TOPICS.items():
        callback_url = f"{base_url}{path}"
        payload = {
            "webhook": {
                "topic": topic,
                "address": callback_url,
                "format": "json",
            }
        }
        try:
            _shopify_request("POST", connection, "/webhooks.json", payload=payload)
            registered += 1
        except requests.HTTPError as exc:
            response_text = getattr(exc.response, "text", "")
            if exc.response is not None and exc.response.status_code in {400, 422} and "address" in response_text:
                registered += 1
                continue
            failed.append({"topic": topic, "error": str(exc)})
        except requests.RequestException as exc:
            failed.append({"topic": topic, "error": str(exc)})

    connection.webhook_status = "active" if not failed else "partial"
    db.add(
        AgentAction(
            action_type="shopify_webhook_registration",
            status=connection.webhook_status,
            payload=_json_dumps({"connection_id": connection.id, "topics": list(SHOPIFY_WEBHOOK_TOPICS)}),
            result=_json_dumps({"registered": registered, "failed": failed}),
        )
    )
    db.commit()
    return {"status": connection.webhook_status, "registered": registered, "failed": failed}


def bootstrap_shopify_connection(db: Session, connection: EcommerceConnection) -> dict:
    result = {"connection_id": connection.id}
    try:
        result["test"] = test_connection(connection)
        db.commit()
        db.refresh(connection)
    except Exception as exc:
        connection.status = "failed"
        db.add(
            AgentAction(
                action_type="shopify_connection_bootstrap_failed",
                status="failed",
                payload=_json_dumps({"connection_id": connection.id, "store_url": connection.store_url}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()
        raise

    bootstrap_steps = [
        ("orders", lambda: sync_orders(db, connection, 50)),
        ("products", lambda: sync_products(db, connection, 100)),
        ("customers", lambda: sync_customers(db, connection, 100)),
    ]
    for step_name, step in bootstrap_steps:
        try:
            result[step_name] = step()
        except Exception as exc:
            result[step_name] = {"status": "skipped", "error": str(exc)}
            db.add(
                AgentAction(
                    action_type=f"shopify_bootstrap_{step_name}_skipped",
                    status="skipped",
                    payload=_json_dumps({"connection_id": connection.id}),
                    result=_json_dumps({"error": str(exc)}),
                )
            )
            db.commit()

    try:
        from app.services.ecommerce_sync import sync_product_catalog_knowledge

        result["knowledge"] = sync_product_catalog_knowledge(db, connection, 100)
    except Exception as exc:
        result["knowledge"] = {"status": "skipped", "error": str(exc)}
        db.add(
            AgentAction(
                action_type="shopify_bootstrap_knowledge_skipped",
                status="skipped",
                payload=_json_dumps({"connection_id": connection.id}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()

    try:
        result["webhooks"] = register_shopify_webhooks(db, connection)
    except Exception as exc:
        result["webhooks"] = {"status": "skipped", "error": str(exc)}
        connection.webhook_status = "failed"
        db.add(
            AgentAction(
                action_type="shopify_bootstrap_webhooks_skipped",
                status="skipped",
                payload=_json_dumps({"connection_id": connection.id}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()

    connection.status = "active"
    db.commit()
    return result


def _phone_from_shopify(order: dict) -> str | None:
    shipping = order.get("shipping_address") or {}
    customer = order.get("customer") or {}
    return order.get("phone") or shipping.get("phone") or customer.get("phone")


def _name_from_shopify(order: dict) -> str | None:
    shipping = order.get("shipping_address") or {}
    customer = order.get("customer") or {}
    first = shipping.get("first_name") or customer.get("first_name") or ""
    last = shipping.get("last_name") or customer.get("last_name") or ""
    return " ".join([first, last]).strip() or None


def _tracking_from_shopify(order: dict) -> tuple[str | None, str | None]:
    for fulfillment in order.get("fulfillments") or []:
        number = fulfillment.get("tracking_number")
        url = fulfillment.get("tracking_url")
        if number or url:
            return number, url
    return None, None


def _tracking_values_from_shopify(order: dict) -> tuple[list[str], list[str], str | None, str | None]:
    numbers = []
    urls = []
    companies = []
    shipment_statuses = []
    for fulfillment in order.get("fulfillments") or []:
        if fulfillment.get("tracking_number"):
            numbers.append(fulfillment.get("tracking_number"))
        if fulfillment.get("tracking_url"):
            urls.append(fulfillment.get("tracking_url"))
        if fulfillment.get("tracking_company"):
            companies.append(fulfillment.get("tracking_company"))
        if fulfillment.get("shipment_status"):
            shipment_statuses.append(fulfillment.get("shipment_status"))
    return numbers, urls, companies[0] if companies else None, shipment_statuses[0] if shipment_statuses else None


def _shopify_customer_name(customer: dict, shipping: dict | None = None) -> str | None:
    shipping = shipping or {}
    first = shipping.get("first_name") or customer.get("first_name") or ""
    last = shipping.get("last_name") or customer.get("last_name") or ""
    return " ".join([first, last]).strip() or customer.get("name")


def upsert_customer(db: Session, connection: EcommerceConnection, customer: dict | None) -> EcommerceCustomer | None:
    if not isinstance(customer, dict) or not customer.get("id"):
        return None
    row = (
        db.query(EcommerceCustomer)
        .filter(
            EcommerceCustomer.connection_id == connection.id,
            EcommerceCustomer.external_id == str(customer.get("id")),
        )
        .first()
    )
    if not row:
        row = EcommerceCustomer(
            tenant_id=connection.tenant_id,
            connection_id=connection.id,
            platform=connection.platform,
            external_id=str(customer.get("id")),
            shopify_customer_id=str(customer.get("id")),
        )
        db.add(row)
    row.tenant_id = connection.tenant_id
    row.name = _shopify_customer_name(customer)
    row.phone = customer.get("phone") or customer.get("default_address", {}).get("phone") or row.phone
    row.email = customer.get("email") or row.email
    row.total_orders = int(customer.get("orders_count") or row.total_orders or 0)
    row.total_spend = str(customer.get("total_spent") or row.total_spend or "")
    row.tags = customer.get("tags")
    row.addresses = _json_dumps(customer.get("addresses") or [])
    row.last_order_at = customer.get("updated_at") or row.last_order_at
    row.marketing_consent = _json_dumps(customer.get("email_marketing_consent") or {})
    row.preferred_language = customer.get("locale") or row.preferred_language
    row.raw_payload = _json_dumps(customer)
    db.commit()
    db.refresh(row)
    return row


def _normalize_shopify_order(order: dict) -> dict:
    tracking_number, tracking_url = _tracking_from_shopify(order)
    tracking_numbers, tracking_urls, courier_company, shipment_status = _tracking_values_from_shopify(order)
    items = [
        {
            "name": item.get("name") or item.get("title"),
            "quantity": item.get("quantity"),
            "sku": item.get("sku"),
            "product_id": item.get("product_id"),
            "variant_id": item.get("variant_id"),
            "price": item.get("price"),
        }
        for item in order.get("line_items", [])
    ]
    customer = order.get("customer") or {}
    return {
        "external_id": str(order.get("id")),
        "shopify_order_id": str(order.get("id")),
        "order_number": str(order.get("name") or order.get("id")),
        "phone": _phone_from_shopify(order),
        "email": order.get("email"),
        "customer_name": _name_from_shopify(order),
        "customer": customer,
        "tags": order.get("tags"),
        "note": order.get("note"),
        "shipping_address": order.get("shipping_address") or {},
        "billing_address": order.get("billing_address") or {},
        "status": order.get("fulfillment_status") or "received",
        "fulfillment_status": order.get("fulfillment_status"),
        "financial_status": order.get("financial_status"),
        "subtotal": str(order.get("subtotal_price") or ""),
        "total": str(order.get("total_price") or ""),
        "discounts": str(order.get("total_discounts") or ""),
        "tax": str(order.get("total_tax") or ""),
        "currency": order.get("currency"),
        "payment_gateway": ", ".join(order.get("payment_gateway_names") or []),
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "tracking_numbers": tracking_numbers,
        "tracking_urls": tracking_urls,
        "courier_company": courier_company,
        "shipment_status": shipment_status,
        "delivery_status": shipment_status if shipment_status == "delivered" else None,
        "skus": [item.get("sku") for item in items if item.get("sku")],
        "product_ids": [item.get("product_id") for item in items if item.get("product_id")],
        "items": items,
        "shopify_created_at": order.get("created_at"),
        "shopify_updated_at": order.get("updated_at"),
    }


def _normalize_woocommerce_order(order: dict) -> dict:
    billing = order.get("billing") or {}
    shipping = order.get("shipping") or {}
    first = shipping.get("first_name") or billing.get("first_name") or ""
    last = shipping.get("last_name") or billing.get("last_name") or ""
    items = [
        {
            "name": item.get("name"),
            "quantity": item.get("quantity"),
            "sku": item.get("sku"),
            "product_id": item.get("product_id"),
            "variant_id": item.get("variation_id"),
        }
        for item in order.get("line_items", [])
    ]
    return {
        "external_id": str(order.get("id")),
        "shopify_order_id": None,
        "order_number": str(order.get("number") or order.get("id")),
        "phone": billing.get("phone"),
        "email": billing.get("email"),
        "customer_name": " ".join([first, last]).strip() or None,
        "customer": {},
        "tags": None,
        "note": order.get("customer_note"),
        "shipping_address": shipping,
        "billing_address": billing,
        "status": order.get("status"),
        "fulfillment_status": order.get("status"),
        "financial_status": order.get("status"),
        "subtotal": str(order.get("subtotal") or ""),
        "total": str(order.get("total") or ""),
        "discounts": str(order.get("discount_total") or ""),
        "tax": str(order.get("total_tax") or ""),
        "currency": order.get("currency"),
        "payment_gateway": order.get("payment_method_title") or order.get("payment_method"),
        "tracking_number": None,
        "tracking_url": None,
        "tracking_numbers": [],
        "tracking_urls": [],
        "courier_company": None,
        "shipment_status": None,
        "delivery_status": None,
        "skus": [item.get("sku") for item in items if item.get("sku")],
        "product_ids": [item.get("product_id") for item in items if item.get("product_id")],
        "items": items,
        "shopify_created_at": order.get("date_created"),
        "shopify_updated_at": order.get("date_modified"),
    }


def _normalize_order(connection: EcommerceConnection, order: dict) -> dict:
    if connection.platform == "shopify":
        return _normalize_shopify_order(order)
    return _normalize_woocommerce_order(order)


def upsert_order(db: Session, connection: EcommerceConnection, order: dict) -> EcommerceOrder:
    normalized = _normalize_order(connection, order)
    customer = upsert_customer(db, connection, normalized.get("customer"))
    row = (
        db.query(EcommerceOrder)
        .filter(
            EcommerceOrder.connection_id == connection.id,
            EcommerceOrder.external_id == normalized["external_id"],
        )
        .first()
    )
    if not row:
        row = EcommerceOrder(
            tenant_id=connection.tenant_id,
            connection_id=connection.id,
            platform=connection.platform,
            external_id=normalized["external_id"],
            order_number=normalized["order_number"],
        )
        db.add(row)

    row.tenant_id = connection.tenant_id
    row.shopify_order_id = normalized["shopify_order_id"]
    row.ecommerce_customer_id = customer.id if customer else row.ecommerce_customer_id
    row.order_number = normalized["order_number"]
    row.phone = normalized["phone"] or row.phone
    row.email = normalized["email"] or row.email
    row.customer_name = normalized["customer_name"] or row.customer_name
    row.tags = normalized["tags"]
    row.note = normalized["note"]
    row.shipping_address = _json_dumps(normalized["shipping_address"])
    row.billing_address = _json_dumps(normalized["billing_address"])
    row.status = normalized["status"]
    row.fulfillment_status = normalized["fulfillment_status"]
    row.financial_status = normalized["financial_status"]
    row.subtotal = normalized["subtotal"]
    row.total = normalized["total"]
    row.discounts = normalized["discounts"]
    row.tax = normalized["tax"]
    row.currency = normalized["currency"]
    row.payment_gateway = normalized["payment_gateway"]
    row.tracking_number = normalized["tracking_number"]
    row.tracking_url = normalized["tracking_url"]
    row.tracking_numbers = _json_dumps(normalized["tracking_numbers"])
    row.tracking_urls = _json_dumps(normalized["tracking_urls"])
    row.courier_company = normalized["courier_company"]
    row.shipment_status = normalized["shipment_status"]
    row.delivery_status = normalized["delivery_status"]
    row.skus = _json_dumps(normalized["skus"])
    row.product_ids = _json_dumps(normalized["product_ids"])
    row.items = _json_dumps(normalized["items"])
    row.raw_payload = _json_dumps(order)
    row.shopify_created_at = normalized["shopify_created_at"]
    row.shopify_updated_at = normalized["shopify_updated_at"]
    db.commit()
    db.refresh(row)
    return row


def sync_orders(db: Session, connection: EcommerceConnection, limit: int = 50) -> dict:
    orders = fetch_orders(connection, limit=limit)
    synced = [upsert_order(db, connection, order) for order in orders]
    connection.last_sync_at = datetime.utcnow()
    db.commit()
    return {"status": "success", "fetched": len(orders), "synced": len(synced)}


def find_order_for_customer(db: Session, phone: str, order_id: str | None = None) -> EcommerceOrder | None:
    query = db.query(EcommerceOrder)
    if order_id:
        normalized_order_id = order_id.strip().lstrip("#")
        query = query.filter(
            (EcommerceOrder.order_number == order_id)
            | (EcommerceOrder.order_number == f"#{normalized_order_id}")
            | (EcommerceOrder.external_id == normalized_order_id)
        )
    else:
        query = query.filter(EcommerceOrder.phone == phone)

    return query.order_by(EcommerceOrder.updated_at.desc()).first()


def order_status_text(order: EcommerceOrder) -> str:
    status = order.fulfillment_status or order.status or "received"
    parts = [f"Your order {order.order_number} status is {status}."]
    if order.tracking_number:
        parts.append(f"Tracking number: {order.tracking_number}.")
    if order.tracking_url:
        parts.append(f"Track here: {order.tracking_url}")
    if order.total:
        parts.append(f"Total: {order.total} {order.currency or ''}".strip())
    return " ".join(parts)


def cross_sell_text(order: EcommerceOrder) -> str:
    items = []
    if order.items:
        try:
            items = [item.get("name") for item in json.loads(order.items) if item.get("name")]
        except json.JSONDecodeError:
            items = []

    if items:
        return (
            f"Thanks for shopping with us. Since you ordered {items[0]}, "
            "you may also like our matching accessories or next best-seller. Reply YES and our team will share options."
        )
    return "Thanks for shopping with us. Reply YES if you want our best new offers and matching product suggestions."


def _raw_payload(order: EcommerceOrder) -> dict:
    if not order.raw_payload:
        return {}
    try:
        data = json.loads(order.raw_payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _status_values(order: EcommerceOrder) -> set[str]:
    values = {
        order.status,
        order.fulfillment_status,
    }
    payload = _raw_payload(order)
    values.update(
        [
            payload.get("status"),
            payload.get("delivery_status"),
            payload.get("shipment_status"),
        ]
    )

    for fulfillment in payload.get("fulfillments") or []:
        if isinstance(fulfillment, dict):
            values.update(
                [
                    fulfillment.get("status"),
                    fulfillment.get("shipment_status"),
                    fulfillment.get("delivery_status"),
                ]
            )

    meta_data = payload.get("meta_data") or []
    for item in meta_data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").lower()
        if "deliver" in key or "shipment" in key or "tracking" in key:
            values.add(item.get("value"))

    return {str(value).strip().lower() for value in values if value}


def is_delivered_order(order: EcommerceOrder) -> bool:
    return bool(_status_values(order) & DELIVERED_STATUSES)


def send_delivered_followups(db: Session, limit: int = 25) -> dict:
    from app.modules.automation.automation_service import (
        TRIGGER_ORDER_DELIVERED,
        enqueue_order_automation_events,
        process_automation_event,
    )

    orders = (
        db.query(EcommerceOrder)
        .filter(EcommerceOrder.delivered_message_sent_at.is_(None))
        .order_by(EcommerceOrder.updated_at.desc())
        .limit(limit)
        .all()
    )

    sent = 0
    skipped = 0
    for order in orders:
        if not is_delivered_order(order) or not order.phone:
            skipped += 1
            continue

        try:
            events = enqueue_order_automation_events(
                db,
                order,
                source="delivered_followup",
                triggers=[TRIGGER_ORDER_DELIVERED],
            )
            results = [process_automation_event(db, event) for event in events]
            was_sent = any(result.get("sent", 0) > 0 for result in results)
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=order.phone,
                    action_type="delivered_followup_failed",
                    status="failed",
                    payload=json.dumps({"order_id": order.order_number}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            skipped += 1
            continue

        if not was_sent:
            skipped += 1
            continue

        order.delivered_message_sent_at = datetime.utcnow()
        db.add(
            AgentAction(
                phone=order.phone,
                action_type="delivered_followup_sent",
                status="sent",
                payload=json.dumps({"order_id": order.order_number}),
                result=json.dumps({"processor": "automation_engine"}),
            )
        )
        db.commit()
        sent += 1

    return {"status": "success", "sent": sent, "skipped": skipped}


def normalize_shop_domain(value: str | None) -> str:
    return (value or "").strip().replace("https://", "").replace("http://", "").strip("/")


def verify_shopify_hmac(raw_body: bytes, hmac_header: str | None) -> bool:
    secret = settings.shopify_webhook_secret
    if not secret:
        return True
    if not hmac_header:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    calculated = base64.b64encode(digest).decode()
    return hmac.compare_digest(calculated, hmac_header)


def find_shopify_connection_by_domain(db: Session, shop_domain: str) -> EcommerceConnection | None:
    domain = normalize_shop_domain(shop_domain)
    return (
        db.query(EcommerceConnection)
        .filter(
            EcommerceConnection.platform == "shopify",
            (
                (EcommerceConnection.myshopify_domain == domain)
                | (EcommerceConnection.store_url == domain)
            ),
        )
        .first()
    )


def record_shopify_webhook_event(
    db: Session,
    connection: EcommerceConnection,
    shop_domain: str,
    topic: str,
    webhook_id: str | None,
    raw_body: bytes,
) -> tuple[ShopifyWebhookEvent, bool]:
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    existing = None
    if webhook_id:
        existing = (
            db.query(ShopifyWebhookEvent)
            .filter(ShopifyWebhookEvent.webhook_id == webhook_id)
            .first()
        )
    if not existing:
        existing = (
            db.query(ShopifyWebhookEvent)
            .filter(
                ShopifyWebhookEvent.connection_id == connection.id,
                ShopifyWebhookEvent.topic == topic,
                ShopifyWebhookEvent.payload_hash == payload_hash,
            )
            .first()
        )
    if existing:
        return existing, existing.status == "processed"

    event = ShopifyWebhookEvent(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        shop_domain=normalize_shop_domain(shop_domain),
        topic=topic,
        webhook_id=webhook_id,
        payload_hash=payload_hash,
        raw_payload=raw_body.decode("utf-8", errors="replace"),
        status="pending",
        attempts=1,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, False


def mark_shopify_webhook_event(
    db: Session,
    event: ShopifyWebhookEvent,
    status: str,
    error: str | None = None,
) -> None:
    event.status = status
    event.error = error
    event.processed_at = datetime.utcnow() if status == "processed" else None
    db.commit()

