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


from app.modules.automation.templates.template_runtime import *

def _conditions_match(rule: AutomationRule, payload: dict) -> bool:
    conditions = _load_json(rule.conditions, {})
    if not conditions:
        return True
    for key, expected in conditions.items():
        if _get_path(payload, key) != expected:
            return False
    return True

def _rule_template(db: Session, rule: AutomationRule) -> MessageTemplate | None:
    if rule.message_template_id:
        template = db.execute(
            select(MessageTemplate).where(MessageTemplate.id == rule.message_template_id)
        ).scalars().first()
        if template and template.status == "active":
            return template
    return None

def _rule_body(db: Session, rule: AutomationRule) -> str:
    template = _rule_template(db, rule)
    if template:
        return template.body
    return rule.message_body or ""

def _send_rule_message(
    db: Session,
    rule: AutomationRule,
    phone: str,
    rendered_message: str,
    context: dict,
) -> dict:
    template = _rule_template(db, rule)
    if template and template.template_type == "whatsapp_template":
        template_name = template.provider_template_name or template.name
        return send_whatsapp_template(
            phone,
            template_name,
            language=template.language or "en",
            body_parameters=_template_body_parameters(template, context),
            button_url_parameters=_template_button_parameters(template, context),
            tenant_id=rule.tenant_id,
        )
    return send_whatsapp_message(phone, rendered_message, tenant_id=rule.tenant_id)

def _message_context(event: AutomationEvent) -> dict:
    payload = _load_json(event.payload, {})
    if not isinstance(payload, dict):
        payload = {}
    context = {
        **payload,
        "external_id": event.external_id or payload.get("external_id") or "",
        "phone": event.phone or payload.get("phone") or "",
        "trigger": event.trigger,
        "source": event.source,
    }
    return _enrich_message_context(context)

__all__ = [
    "_conditions_match",
    "_rule_template",
    "_rule_body",
    "_send_rule_message",
    "_message_context",
]
