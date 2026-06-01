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
TRIGGER_POST_DISPATCH_CROSS_SELL = "post_dispatch_cross_sell"
TRIGGER_DELIVERED_REVIEW = "delivered_review"
TRIGGER_REPLENISHMENT = "replenishment"
TRIGGER_BROWSE_NO_BUY = "browse_no_buy"
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
    {
        "name": "Post Dispatch Cross-sell",
        "trigger": TRIGGER_POST_DISPATCH_CROSS_SELL,
        "template_name": "post_dispatch_cross_sell",
        "body_variable_order": ["customer_name", "product_name"],
        "message_body": "Your {{product_name}} is on the way. Reply YES to see matching picks.",
        "delay_seconds": 86400,
    },
    {
        "name": "Delivered Review",
        "trigger": TRIGGER_DELIVERED_REVIEW,
        "template_name": "delivered_review",
        "body_variable_order": ["customer_name", "order_number"],
        "message_body": "Hope you are loving order {{order_number}}. Reply with a rating from 1 to 5.",
        "delay_seconds": 86400,
    },
    {
        "name": "Replenishment",
        "trigger": TRIGGER_REPLENISHMENT,
        "template_name": "replenishment",
        "body_variable_order": ["customer_name", "product_name"],
        "message_body": "Running low on {{product_name}}? Reply YES to reorder or see refills.",
        "delay_seconds": 7776000,
    },
    {
        "name": "Browse No Buy",
        "trigger": TRIGGER_BROWSE_NO_BUY,
        "template_name": "browse_no_buy",
        "body_variable_order": ["customer_name"],
        "message_body": "Still thinking it over? Reply YES and I will bring back the products you viewed.",
        "delay_seconds": 259200,
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


from app.modules.automation.rules.definition_service import *
from app.modules.automation.events.event_processor_service import create_automation_event

def _first_order_item(order: EcommerceOrder) -> str:
    items = _load_json(order.items, [])
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            return first.get("name") or first.get("title") or ""
    return ""

def order_payload(order: EcommerceOrder) -> dict:
    return {
        "order_id": order.external_id,
        "order_number": order.order_number,
        "phone": order.phone,
        "email": order.email,
        "customer_name": order.customer_name or "there",
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "total": order.total or "",
        "currency": order.currency or "",
        "tracking_number": order.tracking_number or "",
        "tracking_url": order.tracking_url or "",
        "product_name": _first_order_item(order),
    }

def triggers_for_order(order: EcommerceOrder) -> list[str]:
    triggers = [TRIGGER_ORDER_CREATED]
    status_values = {
        str(order.status or "").lower(),
        str(order.fulfillment_status or "").lower(),
        str(order.financial_status or "").lower(),
    }
    if "paid" in status_values:
        triggers.append(TRIGGER_ORDER_PAID)
    if order.tracking_number or order.tracking_url or status_values & {"fulfilled", "shipped"}:
        triggers.append(TRIGGER_ORDER_SHIPPED)
        triggers.append(TRIGGER_POST_DISPATCH_CROSS_SELL)
    if status_values & {"delivered"}:
        triggers.append(TRIGGER_ORDER_DELIVERED)
        triggers.append(TRIGGER_FEEDBACK_REQUEST)
        triggers.append(TRIGGER_DELIVERED_REVIEW)
        triggers.append(TRIGGER_REPLENISHMENT)
    if order.financial_status and order.financial_status.lower() in {"pending", "cod", "cash_on_delivery"}:
        triggers.append(TRIGGER_COD_VERIFICATION)
    return list(dict.fromkeys(triggers))

def enqueue_order_automation_events(
    db: Session,
    order: EcommerceOrder,
    source: str = "ecommerce_webhook",
    triggers: list[str] | None = None,
) -> list[AutomationEvent]:
    payload = order_payload(order)
    events = []
    for trigger in triggers or triggers_for_order(order):
        event = create_automation_event(
            db,
            trigger=trigger,
            source=source,
            external_id=f"{order.platform}:{order.external_id}:{trigger}",
            phone=order.phone,
            payload=payload,
        )
        events.append(event)
    return events

def create_abandoned_cart_event(
    db: Session,
    payload: dict,
    source: str = "ecommerce_webhook",
    delay_seconds: int = 0,
) -> AutomationEvent:
    return create_automation_event(
        db,
        trigger=TRIGGER_CART_ABANDONED,
        source=source,
        external_id=payload.get("external_id") or payload.get("checkout_id") or payload.get("cart_id"),
        phone=payload.get("phone"),
        payload={
            "customer_name": payload.get("customer_name") or payload.get("name") or "there",
            "phone": payload.get("phone"),
            "cart_url": payload.get("cart_url") or payload.get("abandoned_checkout_url") or "",
            "total": payload.get("total") or payload.get("total_price") or "",
            "currency": payload.get("currency") or "",
            "items": payload.get("items") or payload.get("line_items") or [],
        },
        delay_seconds=delay_seconds,
    )

__all__ = [
    "_first_order_item",
    "order_payload",
    "triggers_for_order",
    "enqueue_order_automation_events",
    "create_abandoned_cart_event",
]
