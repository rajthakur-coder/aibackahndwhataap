import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.automation import AutomationEvent, AutomationExecution, AutomationRule, MessageTemplate
from app.models.ecommerce import EcommerceOrder
from app.models.crm import AgentAction
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.client.client_service import send_whatsapp_message, send_whatsapp_template


TRIGGER_ORDER_CREATED = "order_created"
TRIGGER_ORDER_PAID = "order_paid"
TRIGGER_ORDER_SHIPPED = "order_shipped"
TRIGGER_ORDER_DELIVERED = "order_delivered"
TRIGGER_CART_ABANDONED = "cart_abandoned"
TRIGGER_COD_VERIFICATION = "cod_verification"
TRIGGER_FEEDBACK_REQUEST = "feedback_request"
PAUSED_SHOPIFY_AUTOMATION_SOURCES = {
    "shopify_checkouts_api",
    "ecommerce_abandoned_cart_webhook",
    "shopify_webhook",
    "shopify_fulfillment_webhook",
    "ecommerce_order_webhook",
}

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_RULES = [
    {
        "name": "Order Confirmation",
        "trigger": TRIGGER_ORDER_CREATED,
        "template_name": "order_confirmation",
        "body_variable_order": ["customer_name", "order_number", "total", "currency"],
        "message_body": (
            "Hi {{customer_name}}, your order {{order_number}} is confirmed. Total amount is "
            "{{total}} {{currency}}. We will update you when it ships."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "COD Verification",
        "trigger": TRIGGER_COD_VERIFICATION,
        "template_name": "cod_verification",
        "body_variable_order": ["customer_name", "order_number", "total", "currency"],
        "message_body": (
            "Hi {{customer_name}}, please reply YES to confirm your COD order "
            "{{order_number}} worth {{total}} {{currency}} today."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Shipping Update",
        "trigger": TRIGGER_ORDER_SHIPPED,
        "template_name": "shipping_update",
        "body_variable_order": ["customer_name", "order_number"],
        "button_variable_order": ["order_number"],
        "message_body": (
            "Good news {{customer_name}}, your order {{order_number}} has shipped. "
            "Tap the button below to track your order."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Delivered Follow-up",
        "trigger": TRIGGER_ORDER_DELIVERED,
        "template_name": "delivered_followup",
        "body_variable_order": ["customer_name", "order_number"],
        "message_body": (
            "Thank you {{customer_name}}! Your order {{order_number}} has been delivered successfully. "
            "Reply with your feedback."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Abandoned Cart Recovery",
        "trigger": TRIGGER_CART_ABANDONED,
        "template_name": "abandoned_cart_recovery",
        "body_variable_order": ["customer_name"],
        "button_variable_order": ["cart_token"],
        "message_body": (
            "Hi {{customer_name}}, you left items in your cart. Tap the button below to complete your order."
        ),
        "delay_seconds": settings.ABANDONED_CART_DELAY_SECONDS,
    },
    {
        "name": "Feedback Request",
        "trigger": TRIGGER_FEEDBACK_REQUEST,
        "template_name": "feedback_request",
        "body_variable_order": ["customer_name", "order_number"],
        "message_body": (
            "Hi {{customer_name}}, how was your experience with order {{order_number}}? "
            "Reply with a rating from 1 to 5."
        ),
        "delay_seconds": 86400,
    },
]

DEFAULT_MARKETING_TEMPLATES = [
    {
        "name": "Product Recommendation",
        "template_name": "product_recommendation",
        "body_variable_order": ["customer_name"],
        "message_body": "Hi {{customer_name}}, we found some products you may like. Reply YES to see recommendations now.",
    },
    {
        "name": "Sale Offer",
        "template_name": "sale_offer",
        "body_variable_order": ["customer_name"],
        "message_body": "Hi {{customer_name}}, our latest offers are live. Reply YES to explore today's deals now.",
    },
]


def bool_to_db(value: bool) -> str:
    return "true" if value else "false"

def db_to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES

def _utcnow_like(value: datetime | None = None) -> datetime:
    now = datetime.now(timezone.utc)
    if value is not None and value.tzinfo is None:
        return now.replace(tzinfo=None)
    return now

def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def _db_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)

def _load_json(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback

def _get_path(data: dict, path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current

def _first_item_name(context: dict) -> str:
    items = context.get("items") or context.get("line_items") or []
    if isinstance(items, str):
        items = _load_json(items, [])
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("presentment_title", "title", "name", "product_title", "sku"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return ""

def _enrich_message_context(context: dict) -> dict:
    enriched = dict(context or {})
    if not str(enriched.get("customer_name") or "").strip():
        enriched["customer_name"] = "there"
    if not str(enriched.get("product_name") or "").strip():
        enriched["product_name"] = _first_item_name(enriched) or "your cart"
    if not enriched.get("cart_token"):
        enriched["cart_token"] = enriched.get("external_id") or _last_url_segment(enriched.get("cart_url"))
    return enriched

def render_template(body: str, context: dict) -> str:
    context = _enrich_message_context(context)
    def replace(match: re.Match) -> str:
        value = _get_path(context, match.group(1))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True)
        return str(value)

    return VARIABLE_RE.sub(replace, body or "").strip()

def _template_body_parameters(template: MessageTemplate, context: dict) -> list[str]:
    context = _enrich_message_context(context)
    variable_order = _load_json(template.body_variable_order, [])
    if not isinstance(variable_order, list):
        return []
    values = []
    for variable in variable_order:
        if not isinstance(variable, str):
            continue
        if (
            context.get("trigger") == TRIGGER_CART_ABANDONED
            and variable == "cart_url"
            and "bestseller" in str(template.body or "").lower()
        ):
            variable = "product_name"
        value = _get_path(context, variable)
        if value is None:
            values.append("")
        elif isinstance(value, (dict, list)):
            values.append(json.dumps(value, ensure_ascii=True))
        else:
            values.append(str(value))
    return values

def _template_button_parameters(template: MessageTemplate, context: dict) -> list[str]:
    context = _enrich_message_context(context)
    template_name = template.provider_template_name or template.name
    button_orders = {
        "shipping_update": ["order_number"],
        "abandoned_cart_recovery": ["cart_token"],
    }
    if template_name.endswith("_abandoned_cart_recovery"):
        button_orders[template_name] = ["cart_token"]
    values = []
    for variable in button_orders.get(template_name, []):
        value = _get_path(context, variable)
        if value is None and variable == "cart_token":
            value = context.get("external_id") or context.get("cart_url")
        if value is None:
            value = ""
        if variable == "order_number":
            value = str(value).lstrip("#")
        values.append(str(value))
    return values

def _last_url_segment(value: str | None) -> str:
    text = str(value or "").rstrip("/")
    if not text:
        return ""
    return text.rsplit("/", 1)[-1]

__all__ = [
    "bool_to_db",
    "db_to_bool",
    "_utcnow_like",
    "_utcnow_naive",
    "_db_naive",
    "_load_json",
    "_get_path",
    "_first_item_name",
    "_enrich_message_context",
    "render_template",
    "_template_body_parameters",
    "_template_button_parameters",
    "_last_url_segment",
]
