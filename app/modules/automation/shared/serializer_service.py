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

def serialize_template(template: MessageTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "body": template.body,
        "channel": template.channel,
        "template_type": template.template_type,
        "provider_template_name": template.provider_template_name,
        "language": template.language,
        "body_variable_order": _load_json(template.body_variable_order, []),
        "status": template.status,
        "created_at": str(template.created_at),
        "updated_at": str(template.updated_at),
    }

def serialize_rule(rule: AutomationRule) -> dict:
    template = getattr(rule, "message_template", None)
    return {
        "id": rule.id,
        "name": rule.name,
        "trigger": rule.trigger,
        "message_template_id": rule.message_template_id,
        "message_template_type": getattr(template, "template_type", None),
        "provider_template_name": getattr(template, "provider_template_name", None),
        "template_language": getattr(template, "language", None),
        "message_body": rule.message_body,
        "delay_seconds": rule.delay_seconds,
        "conditions": _load_json(rule.conditions, {}),
        "variable_mappings": _load_json(rule.variable_mappings, {}),
        "enabled": db_to_bool(rule.enabled),
        "created_at": str(rule.created_at),
        "updated_at": str(rule.updated_at),
    }

def serialize_event(event: AutomationEvent) -> dict:
    return {
        "id": event.id,
        "trigger": event.trigger,
        "source": event.source,
        "external_id": event.external_id,
        "phone": event.phone,
        "payload": _load_json(event.payload, {}),
        "status": event.status,
        "scheduled_for": str(event.scheduled_for) if event.scheduled_for else None,
        "processed_at": str(event.processed_at) if event.processed_at else None,
        "error": event.error,
        "created_at": str(event.created_at),
        "updated_at": str(event.updated_at),
    }

def serialize_execution(execution: AutomationExecution) -> dict:
    return {
        "id": execution.id,
        "event_id": execution.event_id,
        "rule_id": execution.rule_id,
        "phone": execution.phone,
        "status": execution.status,
        "rendered_message": execution.rendered_message,
        "provider_response": _load_json(execution.provider_response, None),
        "error": execution.error,
        "created_at": str(execution.created_at),
        "sent_at": str(execution.sent_at) if execution.sent_at else None,
    }

__all__ = [
    "serialize_template",
    "serialize_rule",
    "serialize_event",
    "serialize_execution",
]
