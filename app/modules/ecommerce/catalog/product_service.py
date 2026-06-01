import json

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceConnection, EcommerceProduct

def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True)

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
    inventory_quantities = [
        int(variant.get("inventory_quantity"))
        for variant in variants
        if isinstance(variant.get("inventory_quantity"), int)
    ]
    stock_quantity = sum(quantity for quantity in inventory_quantities if quantity > 0)
    in_stock = any(_shopify_variant_available(variant) for variant in variants) if variants else product.get("status") == "active"
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
        "in_stock": in_stock,
        "stock_quantity": stock_quantity if inventory_quantities else None,
        "availability_label": "In stock" if in_stock else "Out of stock",
        "variants": variants,
        "options": product.get("options") or [],
        "seo_title": product.get("seo_title") or product.get("title"),
        "seo_description": product.get("seo_description"),
        "image_urls": [image.get("src") for image in images if image.get("src")],
    }

def _shopify_variant_available(variant: dict) -> bool:
    if not variant:
        return False
    if variant.get("inventory_policy") == "continue":
        return True
    quantity = variant.get("inventory_quantity")
    if isinstance(quantity, int):
        return quantity > 0
    return bool(variant.get("available") or variant.get("requires_shipping") is False)

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
        "in_stock": product.get("stock_status") in {None, "instock", "onbackorder"} or bool(product.get("in_stock")),
        "stock_quantity": product.get("stock_quantity"),
        "availability_label": "In stock" if product.get("stock_status") in {None, "instock", "onbackorder"} or bool(product.get("in_stock")) else "Out of stock",
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
    row = EcommerceProduct(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        platform=connection.platform,
        external_id=normalized["external_id"],
        title=normalized["title"],
    )
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
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Products are read directly from the ecommerce API and cached in Redis; they are not stored in Neon.",
        "connection_id": connection.id,
    }

def sync_inventory(db: Session, connection: EcommerceConnection, limit: int = 100) -> dict:
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Inventory is read directly from the ecommerce API when needed; it is not stored in Neon.",
        "connection_id": connection.id,
    }
