from typing import Protocol

import requests

from app.models.ecommerce import EcommerceConnection
from app.modules.ecommerce.providers.shopify.http_client import _woocommerce_base_url
from app.modules.ecommerce.shared.token_service import decrypt_token


class OMSAdapter(Protocol):
    def get_order(self, order_id: str) -> dict | None: ...
    def list_orders(self, phone: str) -> list[dict]: ...
    def initiate_return(self, order_id: str, items: list[dict], reason: str | None = None) -> dict: ...
    def get_product(self, sku: str) -> dict | None: ...
    def search_catalog(self, query: str, filters: dict | None = None) -> list[dict]: ...
    def create_draft_order(self, items: list[dict], customer: dict) -> dict: ...
    def get_customer(self, phone: str) -> dict | None: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, type] = {}

    def register(self, platform: str, adapter_cls: type) -> None:
        self._adapters[platform.strip().lower()] = adapter_cls

    def get(self, platform: str):
        return self._adapters.get(platform.strip().lower())

    def for_connection(self, connection: EcommerceConnection) -> OMSAdapter | None:
        adapter_cls = self.get(connection.platform or "")
        if not adapter_cls:
            return None
        return adapter_cls(connection)

    def list_platforms(self) -> list[str]:
        return sorted(self._adapters)


oms_adapter_registry = AdapterRegistry()


class APIBackedOMSAdapter:
    def __init__(self, connection: EcommerceConnection) -> None:
        self.connection = connection

    def get_order(self, order_id: str) -> dict | None:
        from app.modules.ecommerce.providers.shopify.order_api import fetch_order_by_id, fetch_order_by_number

        payload = fetch_order_by_number(self.connection, order_id)
        if payload:
            return payload
        if str(order_id or "").strip().lstrip("#").isdigit():
            return fetch_order_by_id(self.connection, str(order_id).strip().lstrip("#"))
        return None

    def list_orders(self, phone: str) -> list[dict]:
        from app.modules.ecommerce.providers.shopify.order_api import fetch_orders

        phone_digits = _digits(phone)
        if not phone_digits:
            return []
        matches = []
        for payload in fetch_orders(self.connection, limit=100):
            candidate_phone = _digits(_phone_from_order(payload))
            if candidate_phone and (candidate_phone.endswith(phone_digits[-10:]) or phone_digits.endswith(candidate_phone[-10:])):
                matches.append(payload)
        return matches

    def initiate_return(self, order_id: str, items: list[dict], reason: str | None = None) -> dict:
        return {
            "status": "needs_manual_processing",
            "order_id": order_id,
            "items": items,
            "reason": reason,
        }

    def get_product(self, sku: str) -> dict | None:
        query = str(sku or "").strip()
        if not query:
            return None
        for product in self.search_catalog(query, {"limit": 20}):
            identifiers = {
                str(product.get("sku") or "").lower(),
                str(product.get("id") or "").lower(),
                str(product.get("external_id") or "").lower(),
                str(product.get("title") or "").lower(),
            }
            skus = product.get("skus") if isinstance(product.get("skus"), list) else []
            identifiers.update(str(item or "").lower() for item in skus)
            if query.lower() in identifiers:
                return product
        results = self.search_catalog(query, {"limit": 1})
        return results[0] if results else None

    def search_catalog(self, query: str, filters: dict | None = None) -> list[dict]:
        from app.modules.ecommerce.providers.shopify.product_api import fetch_products

        filters = filters or {}
        limit = _limit(filters.get("limit"), default=20, maximum=100)
        products = fetch_products(self.connection, limit=max(limit, 100))
        scored = [
            (_score_product(query, product), _normalize_api_product(self.connection, product))
            for product in products
        ]
        return [
            product
            for score, product in sorted(scored, key=lambda item: item[0], reverse=True)
            if score > 0
        ][:limit]

    def create_draft_order(self, items: list[dict], customer: dict) -> dict:
        return {
            "status": "needs_checkout_adapter",
            "platform": self.connection.platform,
            "items": items,
            "customer": customer,
        }

    def get_customer(self, phone: str) -> dict | None:
        phone_digits = _digits(phone)
        if not phone_digits:
            return None
        matches = self.list_orders(phone)
        for order in matches:
            customer = order.get("customer") or order.get("customer_id") or {}
            if isinstance(customer, dict):
                return customer
        return None


class ShopifyOMSAdapter(APIBackedOMSAdapter):
    def get_customer(self, phone: str) -> dict | None:
        from app.modules.ecommerce.providers.shopify.product_api import fetch_customers

        phone_digits = _digits(phone)
        if not phone_digits:
            return None
        for customer in fetch_customers(self.connection, limit=250):
            candidate = _digits(customer.get("phone") or (customer.get("default_address") or {}).get("phone"))
            if candidate and (candidate.endswith(phone_digits[-10:]) or phone_digits.endswith(candidate[-10:])):
                return customer
        return super().get_customer(phone)

    def initiate_return(self, order_id: str, items: list[dict], reason: str | None = None) -> dict:
        from app.modules.ecommerce.providers.shopify.checkout_service import update_shopify_order_return_note

        return update_shopify_order_return_note(
            self.connection,
            order_id=order_id,
            note=f"Return requested from WhatsApp. Reason: {reason or ''}",
        )

    def create_draft_order(self, items: list[dict], customer: dict) -> dict:
        from app.modules.ecommerce.providers.shopify.checkout_service import create_shopify_draft_order

        return create_shopify_draft_order(
            self.connection,
            line_items=items,
            phone=str(customer.get("phone") or ""),
            email=customer.get("email"),
            discount=customer.get("discount"),
            note=customer.get("note"),
        )


class WooCommerceOMSAdapter(APIBackedOMSAdapter):
    def get_customer(self, phone: str) -> dict | None:
        phone_digits = _digits(phone)
        if not phone_digits:
            return None
        response = requests.get(
            f"{_woocommerce_base_url(self.connection)}/customers",
            auth=(self.connection.consumer_key or "", self.connection.consumer_secret or ""),
            params={"search": phone_digits[-10:], "per_page": 20},
            timeout=30,
        )
        response.raise_for_status()
        for customer in response.json():
            billing = customer.get("billing") or {}
            candidate = _digits(customer.get("phone") or billing.get("phone"))
            if candidate and (candidate.endswith(phone_digits[-10:]) or phone_digits.endswith(candidate[-10:])):
                return customer
        return super().get_customer(phone)

    def initiate_return(self, order_id: str, items: list[dict], reason: str | None = None) -> dict:
        note = "Return requested from WhatsApp."
        if reason:
            note = f"{note} Reason: {reason}"
        payload = {"note": note, "customer_note": note}
        response = requests.put(
            f"{_woocommerce_base_url(self.connection)}/orders/{str(order_id).strip().lstrip('#')}",
            auth=(self.connection.consumer_key or "", self.connection.consumer_secret or ""),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def create_draft_order(self, items: list[dict], customer: dict) -> dict:
        line_items = []
        fee_lines = []
        for item in items:
            quantity = max(1, int(item.get("qty") or item.get("quantity") or 1))
            product_id = _int_or_none(item.get("external_id") or item.get("product_id"))
            variation_id = _int_or_none(item.get("variant_id") or item.get("variation_id"))
            if product_id:
                row = {"product_id": product_id, "quantity": quantity}
                if variation_id:
                    row["variation_id"] = variation_id
                line_items.append(row)
                continue
            fee_lines.append(
                {
                    "name": item.get("title") or item.get("sku") or "WhatsApp cart item",
                    "total": str(float(item.get("price") or item.get("price_min") or 0) * quantity),
                }
            )

        payload = {
            "status": "pending",
            "payment_method": "",
            "payment_method_title": "Pending payment",
            "set_paid": False,
            "line_items": line_items,
            "fee_lines": fee_lines,
            "meta_data": [{"key": "source", "value": "whatsapp_ai_cart"}],
        }
        if customer.get("email") or customer.get("phone"):
            payload["billing"] = {
                "email": customer.get("email") or "",
                "phone": customer.get("phone") or "",
            }
        discount = customer.get("discount") or {}
        if discount.get("code"):
            payload["coupon_lines"] = [{"code": str(discount.get("code"))}]

        response = requests.post(
            f"{_woocommerce_base_url(self.connection)}/orders",
            auth=(self.connection.consumer_key or "", self.connection.consumer_secret or ""),
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        order = response.json()
        order_id = order.get("id")
        checkout_url = order.get("payment_url") or ""
        if not checkout_url and order_id:
            checkout_url = f"{self.connection.store_url.rstrip('/')}/checkout/order-pay/{order_id}/?pay_for_order=true&key={order.get('order_key') or ''}"
        return {**order, "checkout_url": checkout_url}


class CustomRESTOMSAdapter(APIBackedOMSAdapter):
    def list_orders(self, phone: str) -> list[dict]:
        data = self._request("GET", "/orders", params={"phone": phone})
        if isinstance(data, list):
            return data
        rows = data.get("orders") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def get_order(self, order_id: str) -> dict | None:
        data = self._request("GET", f"/orders/{str(order_id).strip().lstrip('#')}")
        return data if isinstance(data, dict) else None

    def initiate_return(self, order_id: str, items: list[dict], reason: str | None = None) -> dict:
        data = self._request(
            "POST",
            "/returns",
            payload={"order_id": order_id, "items": items, "reason": reason},
        )
        return data if isinstance(data, dict) else {"status": "submitted", "result": data}

    def get_product(self, sku: str) -> dict | None:
        clean = str(sku or "").strip()
        if not clean:
            return None
        data = self._request("GET", f"/products/{clean}")
        return data if isinstance(data, dict) else super().get_product(sku)

    def search_catalog(self, query: str, filters: dict | None = None) -> list[dict]:
        filters = filters or {}
        data = self._request("GET", "/products", params={"search": query, **filters})
        if isinstance(data, list):
            return data
        rows = data.get("products") if isinstance(data, dict) else []
        return rows if isinstance(rows, list) else []

    def create_draft_order(self, items: list[dict], customer: dict) -> dict:
        data = self._request("POST", "/draft-orders", payload={"items": items, "customer": customer})
        return data if isinstance(data, dict) else {"status": "created", "result": data}

    def get_customer(self, phone: str) -> dict | None:
        data = self._request("GET", "/customers", params={"phone": phone})
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            rows = data.get("customers")
            if isinstance(rows, list):
                return rows[0] if rows else None
            return data
        return None

    def _request(self, method: str, path: str, params: dict | None = None, payload: dict | None = None):
        base_url = str(self.connection.store_url or "").rstrip("/")
        if not base_url:
            raise RuntimeError("Custom REST OMS store_url is not configured")
        token = decrypt_token(self.connection.encrypted_access_token) or self.connection.access_token or ""
        response = requests.request(
            method,
            f"{base_url}{path}",
            params=params,
            json=payload,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=30,
        )
        response.raise_for_status()
        if not response.text:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text[:4000]}


def _digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _phone_from_order(order: dict) -> str:
    shipping = order.get("shipping_address") or order.get("shipping") or {}
    billing = order.get("billing_address") or order.get("billing") or {}
    customer = order.get("customer") or {}
    return str(
        order.get("phone")
        or shipping.get("phone")
        or billing.get("phone")
        or customer.get("phone")
        or ""
    )


def _int_or_none(value) -> int | None:
    try:
        parsed = int(str(value or "").strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _limit(value, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _score_product(query: str, product: dict) -> int:
    query_text = str(query or "").strip().lower()
    if not query_text:
        return 1
    haystack = " ".join(
        str(value or "")
        for value in (
            product.get("title"),
            product.get("name"),
            product.get("sku"),
            product.get("id"),
            product.get("product_type"),
            product.get("type"),
            product.get("tags"),
        )
    ).lower()
    variants = product.get("variants") or []
    if isinstance(variants, list):
        haystack += " " + " ".join(str(variant.get("sku") or "") for variant in variants if isinstance(variant, dict))
    terms = [term for term in query_text.replace("-", " ").split() if term]
    return sum(3 if term in haystack else 0 for term in terms) + (5 if query_text in haystack else 0)


def _normalize_api_product(connection: EcommerceConnection, product: dict) -> dict:
    if connection.platform == "woocommerce":
        images = product.get("images") or []
        return {
            "id": product.get("id"),
            "external_id": str(product.get("id") or ""),
            "sku": product.get("sku"),
            "skus": [product.get("sku")] if product.get("sku") else [],
            "title": product.get("name"),
            "description": product.get("short_description") or product.get("description"),
            "product_url": product.get("permalink"),
            "price_min": product.get("price") or product.get("regular_price"),
            "price_max": product.get("price") or product.get("regular_price"),
            "currency": connection.currency,
            "status": product.get("status"),
            "inventory": product.get("stock_status") or product.get("stock_quantity"),
            "image_url": (images[0] or {}).get("src") if images else None,
            "image_urls": [image.get("src") for image in images if image.get("src")],
            "raw": product,
        }
    variants = product.get("variants") or []
    images = product.get("images") or []
    skus = [variant.get("sku") for variant in variants if isinstance(variant, dict) and variant.get("sku")]
    prices = [variant.get("price") for variant in variants if isinstance(variant, dict) and variant.get("price")]
    return {
        "id": product.get("id"),
        "external_id": str(product.get("id") or ""),
        "sku": ", ".join(skus[:10]) if skus else None,
        "skus": skus,
        "title": product.get("title"),
        "description": product.get("body_html"),
        "product_url": f"https://{connection.store_url}/products/{product.get('handle')}" if product.get("handle") else None,
        "price_min": min(prices) if prices else None,
        "price_max": max(prices) if prices else None,
        "currency": connection.currency,
        "status": product.get("status"),
        "inventory": ", ".join(str(variant.get("inventory_quantity")) for variant in variants if isinstance(variant, dict) and variant.get("inventory_quantity") is not None),
        "image_url": (images[0] or {}).get("src") if images else None,
        "image_urls": [image.get("src") for image in images if image.get("src")],
        "raw": product,
    }


oms_adapter_registry.register("shopify", ShopifyOMSAdapter)
oms_adapter_registry.register("woocommerce", WooCommerceOMSAdapter)
oms_adapter_registry.register("custom_rest", CustomRESTOMSAdapter)
