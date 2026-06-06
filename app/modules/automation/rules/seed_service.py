import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.automation import AutomationEvent, AutomationExecution, AutomationRule, MessageTemplate
from app.models.ecommerce import EcommerceOrder
from app.models.crm import AgentAction
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.client.client_service import send_whatsapp_message, send_whatsapp_template
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


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


def ensure_default_automation_rules(db: Session) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    created = 0
    templates_created = 0
    templates_updated = 0
    for rule_data in DEFAULT_RULES:
        template, template_created, template_updated = _ensure_message_template(db, rule_data)
        templates_created += int(template_created)
        templates_updated += int(template_updated)
        exists = db.execute(
            select(AutomationRule).where(AutomationRule.tenant_id == tenant_id, AutomationRule.name == rule_data["name"])
        ).scalars().first()
        if exists:
            if exists.message_template_id != template.id:
                exists.message_template_id = template.id
                exists.message_body = None
            if exists.delay_seconds != rule_data["delay_seconds"]:
                exists.delay_seconds = rule_data["delay_seconds"]
            if exists.enabled != "true":
                exists.enabled = "true"
            continue
        db.add(
            AutomationRule(
                tenant_id=tenant_id,
                name=rule_data["name"],
                trigger=rule_data["trigger"],
                message_template_id=template.id,
                message_body=None,
                delay_seconds=rule_data["delay_seconds"],
                enabled="true",
            )
        )
        created += 1
    for template_data in DEFAULT_MARKETING_TEMPLATES:
        _template, template_created, template_updated = _ensure_message_template(db, template_data)
        templates_created += int(template_created)
        templates_updated += int(template_updated)
    db.commit()
    return {
        "status": "success",
        "created": created,
        "templates_created": templates_created,
        "templates_updated": templates_updated,
    }

def _ensure_message_template(db: Session, data: dict) -> tuple[MessageTemplate, bool, bool]:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    provider_name = data["template_name"]
    row = db.execute(
        select(MessageTemplate).where(
            MessageTemplate.tenant_id == tenant_id,
            or_(
                MessageTemplate.name == provider_name,
                MessageTemplate.provider_template_name == provider_name,
            ),
        )
    ).scalars().first()
    created = False
    updated = False
    if not row:
        row = MessageTemplate(
            tenant_id=tenant_id,
            name=_tenant_safe_template_name(db, tenant_id, provider_name),
            body=data["message_body"],
        )
        db.add(row)
        created = True
    desired_order = json.dumps(data.get("body_variable_order") or [], ensure_ascii=True)
    for field, value in {
        "body": data["message_body"],
        "channel": "whatsapp",
        "template_type": "whatsapp_template",
        "provider_template_name": provider_name,
        "language": "en",
        "body_variable_order": desired_order,
        "status": "active",
    }.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            updated = True
    db.flush()
    return row, created, updated and not created


def _tenant_safe_template_name(db: Session, tenant_id: str, provider_name: str) -> str:
    existing = db.execute(
        select(MessageTemplate).where(MessageTemplate.name == provider_name)
    ).scalars().first()
    if not existing or existing.tenant_id == tenant_id:
        return provider_name
    return f"{tenant_id}:{provider_name}"[:255]

__all__ = [
    "ensure_default_automation_rules",
    "_ensure_message_template",
    "_tenant_safe_template_name",
]
