import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import AgentAction
from app.models.ecommerce import (
    ContactStoreMapping,
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
)

DELIVERED_STATUSES = {"delivered"}


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True)

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

def _digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())

__all__ = [
    "_json_dumps",
    "_phone_from_shopify",
    "_name_from_shopify",
    "_tracking_from_shopify",
    "_tracking_values_from_shopify",
    "_shopify_customer_name",
    "_normalize_shopify_order",
    "_normalize_woocommerce_order",
    "_normalize_order",
    "_digits",
]
