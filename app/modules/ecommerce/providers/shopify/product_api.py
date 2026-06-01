import time
from urllib.parse import urlparse

import requests
from requests.utils import parse_header_links

from app.config import settings
from app.models.ecommerce import EcommerceConnection
from app.modules.ecommerce.shared.token_service import (
    decrypt_token as _decrypt_token,
)

REQUEST_TIMEOUT = 30
SHOPIFY_API_VERSION = "2025-04"

from app.modules.ecommerce.providers.shopify.http_client import *

def fetch_all_products(connection: EcommerceConnection, limit: int = 5000) -> list[dict]:
    limit = max(1, min(limit, 5000))
    if connection.platform == "shopify":
        products = []
        page_info = None
        fields = "id,title,handle,body_html,vendor,product_type,tags,status,variants,images,options,created_at,updated_at"
        while len(products) < limit:
            params = {"limit": min(250, limit - len(products)), "fields": fields}
            if page_info:
                params = {"limit": min(250, limit - len(products)), "page_info": page_info}
            response = _shopify_request("GET", connection, "/products.json", params=params)
            products.extend(response.json().get("products", []))
            page_info = _next_page_info(response)
            if not page_info:
                break
        return products[:limit]

    products = []
    page = 1
    while len(products) < limit:
        response = requests.get(
            f"{_woocommerce_base_url(connection)}/products",
            auth=(connection.consumer_key or "", connection.consumer_secret or ""),
            params={"per_page": min(100, limit - len(products)), "page": page, "orderby": "date", "order": "desc"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        products.extend(batch)
        page += 1
    return products[:limit]

def fetch_shopify_collections(connection: EcommerceConnection, limit: int = 250) -> list[dict]:
    if connection.platform != "shopify":
        return []

    limit = max(1, min(limit, 250))
    collections = []
    for path in ("/custom_collections.json", "/smart_collections.json"):
        page_info = None
        while len(collections) < limit:
            params = {"limit": min(250, limit - len(collections)), "fields": "id,title,handle,updated_at,published_at"}
            if page_info:
                params = {"limit": min(250, limit - len(collections)), "page_info": page_info}
            response = _shopify_request("GET", connection, path, params=params)
            key = "custom_collections" if path.startswith("/custom") else "smart_collections"
            collections.extend(response.json().get(key, []))
            page_info = _next_page_info(response)
            if not page_info:
                break
    return collections[:limit]

def fetch_shopify_collects(connection: EcommerceConnection, limit: int = 5000) -> list[dict]:
    if connection.platform != "shopify":
        return []

    limit = max(1, min(limit, 5000))
    collects = []
    page_info = None
    while len(collects) < limit:
        params = {"limit": min(250, limit - len(collects)), "fields": "id,collection_id,product_id,featured,position,updated_at"}
        if page_info:
            params = {"limit": min(250, limit - len(collects)), "page_info": page_info}
        response = _shopify_request("GET", connection, "/collects.json", params=params)
        collects.extend(response.json().get("collects", []))
        page_info = _next_page_info(response)
        if not page_info:
            break
    return collects[:limit]

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

def fetch_locations(connection: EcommerceConnection) -> list[dict]:
    if connection.platform != "shopify":
        return []
    response = _shopify_request("GET", connection, "/locations.json")
    return response.json().get("locations", [])

def fetch_inventory_levels(
    connection: EcommerceConnection,
    inventory_item_ids: list[str] | None = None,
    location_ids: list[str] | None = None,
    limit: int = 250,
) -> list[dict]:
    if connection.platform != "shopify":
        return []
    params = {"limit": max(1, min(limit, 250))}
    if inventory_item_ids:
        params["inventory_item_ids"] = ",".join(str(item_id) for item_id in inventory_item_ids if item_id)
    if location_ids:
        params["location_ids"] = ",".join(str(location_id) for location_id in location_ids if location_id)
    if not params.get("inventory_item_ids") and not params.get("location_ids"):
        locations = fetch_locations(connection)
        params["location_ids"] = ",".join(str(location.get("id")) for location in locations if location.get("id"))
    if not params.get("inventory_item_ids") and not params.get("location_ids"):
        return []

    levels = []
    page_info = None
    while True:
        request_params = {"limit": params["limit"], "page_info": page_info} if page_info else params
        response = _shopify_request("GET", connection, "/inventory_levels.json", params=request_params)
        levels.extend(response.json().get("inventory_levels", []))
        page_info = _next_page_info(response)
        if not page_info:
            break
    return levels

def fetch_abandoned_checkouts(connection: EcommerceConnection, limit: int = 50) -> list[dict]:
    if connection.platform != "shopify":
        return []
    checkouts = []
    page_info = None
    while len(checkouts) < max(1, min(limit, 250)):
        params = {
            "limit": min(250, max(1, min(limit, 250)) - len(checkouts)),
            "status": "open",
            "fields": "id,token,email,phone,abandoned_checkout_url,total_price,currency,line_items,customer,shipping_address,billing_address,created_at,updated_at",
        }
        if page_info:
            params = {"limit": min(250, max(1, min(limit, 250)) - len(checkouts)), "page_info": page_info}
        response = _shopify_request("GET", connection, "/checkouts.json", params=params)
        checkouts.extend(response.json().get("checkouts", []))
        page_info = _next_page_info(response)
        if not page_info:
            break
    return checkouts[: max(1, min(limit, 250))]

def fetch_fulfillments(connection: EcommerceConnection, order_id: str) -> list[dict]:
    if connection.platform != "shopify":
        return []
    response = _shopify_request("GET", connection, f"/orders/{order_id}/fulfillments.json")
    return response.json().get("fulfillments", [])

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

__all__ = [
    "fetch_all_products",
    "fetch_shopify_collections",
    "fetch_shopify_collects",
    "fetch_products",
    "fetch_locations",
    "fetch_inventory_levels",
    "fetch_abandoned_checkouts",
    "fetch_fulfillments",
    "fetch_customers",
]
