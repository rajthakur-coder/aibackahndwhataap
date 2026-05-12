import json
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.entities import AgentAction, EcommerceConnection, EcommerceOrder, EcommerceProduct
REQUEST_TIMEOUT = 30
SHOPIFY_API_VERSION = "2025-04"
SUPPORTED_PLATFORMS = {"shopify", "woocommerce"}
DELIVERED_STATUSES = {"delivered"}


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

    connection = EcommerceConnection(
        name=name.strip() or store_url,
        platform=platform,
        store_url=store_url,
        access_token=access_token,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
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
        "X-Shopify-Access-Token": connection.access_token or "",
        "Content-Type": "application/json",
    }


def _shopify_base_url(connection: EcommerceConnection) -> str:
    return f"https://{connection.store_url}/admin/api/{SHOPIFY_API_VERSION}"


def _woocommerce_base_url(connection: EcommerceConnection) -> str:
    return f"{connection.store_url}/wp-json/wc/v3"


def test_connection(connection: EcommerceConnection) -> dict:
    if connection.platform == "shopify":
        response = requests.get(
            f"{_shopify_base_url(connection)}/shop.json",
            headers=_shopify_headers(connection),
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        shop = response.json().get("shop", {})
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
        response = requests.get(
            f"{_shopify_base_url(connection)}/orders.json",
            headers=_shopify_headers(connection),
            params={
                "status": "any",
                "limit": limit,
                "fields": "id,name,email,phone,total_price,currency,financial_status,fulfillment_status,line_items,shipping_address,customer,fulfillments,created_at,updated_at",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("orders", [])

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
        response = requests.get(
            f"{_shopify_base_url(connection)}/products.json",
            headers=_shopify_headers(connection),
            params={
                "limit": limit,
                "fields": "id,title,handle,body_html,vendor,product_type,tags,status,variants,images,options,created_at,updated_at",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("products", [])

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/products",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        params={"per_page": min(limit, 100), "orderby": "date", "order": "desc"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


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
        "title": product.get("title") or "Untitled product",
        "handle": handle,
        "product_url": f"https://{connection.store_url}/products/{handle}" if handle else None,
        "description": _plain_text(product.get("body_html")),
        "vendor": product.get("vendor"),
        "product_type": product.get("product_type"),
        "tags": product.get("tags"),
        "status": product.get("status"),
        "price_min": price_min,
        "price_max": price_max,
        "currency": None,
        "sku": ", ".join(skus[:10]) if skus else None,
        "inventory": ", ".join(inventory_values[:20]) if inventory_values else None,
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
        "title": product.get("name") or "Untitled product",
        "handle": product.get("slug"),
        "product_url": product.get("permalink"),
        "description": _plain_text(product.get("description") or product.get("short_description")),
        "vendor": None,
        "product_type": categories or product.get("type"),
        "tags": tags,
        "status": product.get("status"),
        "price_min": price_min,
        "price_max": price_max,
        "currency": None,
        "sku": product.get("sku"),
        "inventory": str(product.get("stock_quantity")) if product.get("stock_quantity") is not None else product.get("stock_status"),
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
            connection_id=connection.id,
            platform=connection.platform,
            external_id=normalized["external_id"],
            title=normalized["title"],
        )
        db.add(row)

    row.title = normalized["title"]
    row.handle = normalized["handle"]
    row.product_url = normalized["product_url"]
    row.description = normalized["description"]
    row.vendor = normalized["vendor"]
    row.product_type = normalized["product_type"]
    row.tags = normalized["tags"]
    row.status = normalized["status"]
    row.price_min = normalized["price_min"]
    row.price_max = normalized["price_max"]
    row.currency = normalized["currency"]
    row.sku = normalized["sku"]
    row.inventory = normalized["inventory"]
    row.image_urls = json.dumps(normalized["image_urls"])
    row.raw_payload = json.dumps(product)
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


def _normalize_shopify_order(order: dict) -> dict:
    tracking_number, tracking_url = _tracking_from_shopify(order)
    items = [
        {
            "name": item.get("name") or item.get("title"),
            "quantity": item.get("quantity"),
            "sku": item.get("sku"),
        }
        for item in order.get("line_items", [])
    ]
    return {
        "external_id": str(order.get("id")),
        "order_number": str(order.get("name") or order.get("id")),
        "phone": _phone_from_shopify(order),
        "email": order.get("email"),
        "customer_name": _name_from_shopify(order),
        "status": order.get("fulfillment_status") or "received",
        "fulfillment_status": order.get("fulfillment_status"),
        "financial_status": order.get("financial_status"),
        "total": str(order.get("total_price") or ""),
        "currency": order.get("currency"),
        "tracking_number": tracking_number,
        "tracking_url": tracking_url,
        "items": items,
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
        }
        for item in order.get("line_items", [])
    ]
    return {
        "external_id": str(order.get("id")),
        "order_number": str(order.get("number") or order.get("id")),
        "phone": billing.get("phone"),
        "email": billing.get("email"),
        "customer_name": " ".join([first, last]).strip() or None,
        "status": order.get("status"),
        "fulfillment_status": order.get("status"),
        "financial_status": order.get("status"),
        "total": str(order.get("total") or ""),
        "currency": order.get("currency"),
        "tracking_number": None,
        "tracking_url": None,
        "items": items,
    }


def _normalize_order(connection: EcommerceConnection, order: dict) -> dict:
    if connection.platform == "shopify":
        return _normalize_shopify_order(order)
    return _normalize_woocommerce_order(order)


def upsert_order(db: Session, connection: EcommerceConnection, order: dict) -> EcommerceOrder:
    normalized = _normalize_order(connection, order)
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
            connection_id=connection.id,
            platform=connection.platform,
            external_id=normalized["external_id"],
            order_number=normalized["order_number"],
        )
        db.add(row)

    row.order_number = normalized["order_number"]
    row.phone = normalized["phone"] or row.phone
    row.email = normalized["email"] or row.email
    row.customer_name = normalized["customer_name"] or row.customer_name
    row.status = normalized["status"]
    row.fulfillment_status = normalized["fulfillment_status"]
    row.financial_status = normalized["financial_status"]
    row.total = normalized["total"]
    row.currency = normalized["currency"]
    row.tracking_number = normalized["tracking_number"]
    row.tracking_url = normalized["tracking_url"]
    row.items = json.dumps(normalized["items"])
    row.raw_payload = json.dumps(order)
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
    from app.services.automations import (
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

