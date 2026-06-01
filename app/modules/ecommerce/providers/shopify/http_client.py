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

def required_shopify_scopes() -> list[str]:
    return settings.SHOPIFY_REQUIRED_SCOPES

def fetch_shopify_access_scopes(connection: EcommerceConnection) -> list[str]:
    if connection.platform != "shopify":
        return []
    domain = connection.myshopify_domain or connection.store_url
    response = requests.get(
        f"https://{domain}/admin/oauth/access_scopes.json",
        headers=_shopify_headers(connection),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    scopes = response.json().get("access_scopes", [])
    return [
        str(scope.get("handle") or "").strip()
        for scope in scopes
        if isinstance(scope, dict) and scope.get("handle")
    ]

def validate_shopify_scopes(connection: EcommerceConnection) -> dict:
    required = required_shopify_scopes()
    granted = fetch_shopify_access_scopes(connection)
    granted_set = set(granted)
    missing = [scope for scope in required if scope not in granted_set]
    return {
        "status": "ok" if not missing else "missing_scopes",
        "required": required,
        "granted": granted,
        "missing": missing,
    }

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
        scopes = validate_shopify_scopes(connection)
        return {
            "ok": scopes["status"] == "ok",
            "platform": "shopify",
            "store": shop.get("name") or connection.store_url,
            "scopes": scopes,
        }

    response = requests.get(
        f"{_woocommerce_base_url(connection)}/system_status",
        auth=(connection.consumer_key or "", connection.consumer_secret or ""),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return {"ok": True, "platform": "woocommerce", "store": data.get("environment", {}).get("site_url") or connection.store_url}

__all__ = [
    "_shopify_headers",
    "_shopify_base_url",
    "_shopify_request",
    "required_shopify_scopes",
    "fetch_shopify_access_scopes",
    "validate_shopify_scopes",
    "_next_page_info",
    "_woocommerce_base_url",
    "test_connection",
]
