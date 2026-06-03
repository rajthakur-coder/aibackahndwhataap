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

def fetch_orders_for_sales(connection: EcommerceConnection, limit: int = 500) -> list[dict]:
    limit = max(1, min(limit, 500))
    if connection.platform == "shopify":
        orders = []
        page_info = None
        fields = (
            "id,name,email,phone,tags,note,subtotal_price,total_price,total_discounts,total_tax,"
            "currency,financial_status,fulfillment_status,line_items,shipping_address,billing_address,"
            "customer,fulfillments,payment_gateway_names,created_at,updated_at"
        )
        while len(orders) < limit:
            params = {
                "status": "any",
                "limit": min(250, limit - len(orders)),
                "fields": fields,
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
        params={"per_page": 100, "orderby": "date", "order": "desc"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()[:limit]

def fetch_order_by_number(connection: EcommerceConnection, order_number: str) -> dict | None:
    clean_order_number = str(order_number or "").strip().lstrip("#")
    if not clean_order_number:
        return None

    if connection.platform == "shopify":
        fields = (
            "id,name,email,phone,tags,note,subtotal_price,total_price,total_discounts,total_tax,"
            "currency,financial_status,fulfillment_status,line_items,shipping_address,billing_address,"
            "customer,fulfillments,payment_gateway_names,created_at,updated_at"
        )
        for name in (f"#{clean_order_number}", clean_order_number):
            response = _shopify_request(
                "GET",
                connection,
                "/orders.json",
                params={"status": "any", "limit": 1, "name": name, "fields": fields},
            )
            orders = response.json().get("orders", [])
            for order in orders:
                if _matches_shopify_order_number(order, clean_order_number):
                    return order
        return _scan_recent_shopify_orders_by_number(connection, clean_order_number, fields)

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/orders",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        params={"search": clean_order_number, "per_page": 10, "orderby": "date", "order": "desc"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    for order in response.json():
        if str(order.get("number") or order.get("id") or "").lstrip("#") == clean_order_number:
            return order
    return None


def _scan_recent_shopify_orders_by_number(
    connection: EcommerceConnection,
    clean_order_number: str,
    fields: str,
) -> dict | None:
    page_info = None
    while True:
        params = {
            "status": "any",
            "limit": 250,
            "fields": fields,
        }
        if page_info:
            params = {
                "limit": 250,
                "page_info": page_info,
                "fields": fields,
            }
        response = _shopify_request("GET", connection, "/orders.json", params=params)
        orders = response.json().get("orders", [])
        if not orders:
            return None
        for order in orders:
            if _matches_shopify_order_number(order, clean_order_number):
                return order
        page_info = _next_page_info(response)
        if not page_info:
            return None
    return None


def _matches_shopify_order_number(order: dict, clean_order_number: str) -> bool:
    expected = str(clean_order_number or "").strip().lstrip("#").upper()
    candidates = {
        str(order.get("name") or "").strip().lstrip("#").upper(),
        str(order.get("order_number") or "").strip().lstrip("#").upper(),
        str(order.get("number") or "").strip().lstrip("#").upper(),
    }
    return expected in candidates

def fetch_order_by_id(connection: EcommerceConnection, order_id: str) -> dict | None:
    clean_order_id = str(order_id or "").strip()
    if not clean_order_id:
        return None

    if connection.platform == "shopify":
        fields = (
            "id,name,email,phone,tags,note,subtotal_price,total_price,total_discounts,total_tax,"
            "currency,financial_status,fulfillment_status,line_items,shipping_address,billing_address,"
            "customer,fulfillments,payment_gateway_names,created_at,updated_at"
        )
        response = _shopify_request(
            "GET",
            connection,
            f"/orders/{clean_order_id}.json",
            params={"fields": fields},
        )
        return response.json().get("order")

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/orders/{clean_order_id}",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else None

__all__ = [
    "fetch_orders",
    "fetch_orders_for_sales",
    "fetch_order_by_number",
    "fetch_order_by_id",
]
