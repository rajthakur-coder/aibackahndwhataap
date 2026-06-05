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
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


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


from app.modules.automation.rules.definition_service import *

def create_automation_event(
    db: Session,
    trigger: str,
    source: str,
    payload: dict,
    phone: str | None = None,
    external_id: str | None = None,
    delay_seconds: int = 0,
) -> AutomationEvent:
    tenant_id = normalize_tenant_id(payload.get("tenant_id") or current_tenant_id() or DEFAULT_TENANT_ID)
    delay_seconds = max(0, delay_seconds)
    if external_id:
        existing = db.execute(
            select(AutomationEvent)
            .where(
                AutomationEvent.tenant_id == tenant_id,
                AutomationEvent.trigger == trigger.strip(),
                AutomationEvent.external_id == external_id,
            )
            .order_by(AutomationEvent.created_at.desc())
        ).scalars().first()
        if existing:
            return existing

    event = AutomationEvent(
        tenant_id=tenant_id,
        trigger=trigger.strip(),
        source=source.strip() or "system",
        external_id=external_id,
        phone=phone or payload.get("phone"),
        payload=json.dumps(payload, ensure_ascii=True),
        status="pending",
        scheduled_for=_utcnow_naive() + timedelta(seconds=delay_seconds),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event

def process_automation_event(db: Session, event: AutomationEvent) -> dict:
    if event.status == "processed":
        return {"status": "skipped", "reason": "already_processed"}
    if (
        not settings.SHOPIFY_WEBHOOK_AUTOMATION_ENABLED
        and str(event.source or "").strip() in PAUSED_SHOPIFY_AUTOMATION_SOURCES
    ):
        event.status = "pending"
        event.error = "Shopify/live ecommerce automation is paused. Enable SHOPIFY_WEBHOOK_AUTOMATION_ENABLED to send queued events."
        db.commit()
        return {
            "status": "skipped",
            "event_id": event.id,
            "reason": "shopify_automation_paused",
            "sent": 0,
            "failed": 0,
            "skipped": 1,
            "pending_delayed": 0,
            "errors": [],
        }
    if event.scheduled_for and event.scheduled_for > _utcnow_like(event.scheduled_for):
        return {"status": "skipped", "reason": "not_due"}

    payload = _message_context(event)
    if _abandoned_cart_was_converted(db, event, payload):
        event.status = "processed"
        event.processed_at = _utcnow_naive()
        event.error = "Skipped abandoned cart reminder because this customer placed an order."
        db.commit()
        return {
            "status": "skipped",
            "event_id": event.id,
            "reason": "abandoned_cart_converted",
            "sent": 0,
            "failed": 0,
            "skipped": 1,
            "pending_delayed": 0,
            "errors": [],
        }

    rules = db.execute(
        select(AutomationRule)
        .where(AutomationRule.tenant_id == event.tenant_id, AutomationRule.trigger == event.trigger)
        .order_by(AutomationRule.created_at.asc())
    ).scalars().all()
    matched_rules = [
        rule
        for rule in rules
        if db_to_bool(rule.enabled) and _conditions_match(rule, payload)
    ]

    sent = 0
    failed = 0
    skipped = 0
    pending_delayed = 0
    errors = []
    event.status = "processing"
    event.error = None
    db.commit()

    for rule in matched_rules:
        existing = db.execute(
            select(AutomationExecution)
            .where(
                AutomationExecution.tenant_id == event.tenant_id,
                AutomationExecution.event_id == event.id,
                AutomationExecution.rule_id == rule.id,
            )
        ).scalars().first()
        if existing:
            skipped += 1
            continue

        due_at = event.created_at + timedelta(seconds=max(0, rule.delay_seconds or 0))
        if due_at > _utcnow_like(due_at):
            event.scheduled_for = _db_naive(due_at)
            pending_delayed += 1
            continue

        message = render_template(_rule_body(db, rule), payload)
        execution = AutomationExecution(
            tenant_id=event.tenant_id,
            event_id=event.id,
            rule_id=rule.id,
            phone=event.phone or payload.get("phone"),
            status="pending",
            rendered_message=message,
        )
        db.add(execution)
        db.commit()
        db.refresh(execution)

        if not execution.phone or not message:
            execution.status = "skipped"
            execution.error = "Missing phone or rendered message"
            db.commit()
            skipped += 1
            continue

        try:
            response = _send_rule_message(db, rule, execution.phone, message, payload)
            execution.status = "sent"
            execution.provider_response = json.dumps(response, ensure_ascii=True)
            execution.sent_at = _utcnow_naive()
            save_message(db, execution.phone, message, "outgoing", tenant_id=event.tenant_id)
            sent += 1
        except Exception as exc:
            execution.status = "failed"
            execution.error = str(exc)
            db.add(
                AgentAction(
                    tenant_id=event.tenant_id,
                    phone=execution.phone,
                    action_type="automation_send_failed",
                    status="failed",
                    payload=json.dumps({"event_id": event.id, "rule_id": rule.id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            failed += 1
            errors.append({"rule_id": rule.id, "error": str(exc)})
        db.commit()

    event.processed_at = _utcnow_naive()
    if pending_delayed:
        event.status = "pending"
        event.processed_at = None
    else:
        event.status = "failed" if failed and not sent else "processed"
    event.error = json.dumps(errors[:5], ensure_ascii=True) if errors else None
    db.commit()
    return {
        "status": event.status,
        "event_id": event.id,
        "matched_rules": len(matched_rules),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "pending_delayed": pending_delayed,
        "errors": errors[:5],
    }

def _abandoned_cart_was_converted(db: Session, event: AutomationEvent, payload: dict) -> bool:
    if event.trigger != TRIGGER_CART_ABANDONED:
        return False

    phone_digits = _digits(event.phone or payload.get("phone"))
    email = _clean_email(payload.get("email"))
    if not phone_digits and not email:
        return False

    cutoff = _event_cutoff(event, payload)
    orders = db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.tenant_id == event.tenant_id)
        .order_by(EcommerceOrder.updated_at.desc(), EcommerceOrder.id.desc())
        .limit(300)
    ).scalars().all()
    for order in orders:
        if not _same_customer_order(order, phone_digits, email):
            continue
        if not _order_after_cutoff(order, cutoff):
            continue
        if _order_is_cancelled(order):
            continue
        return True
    return False

def _same_customer_order(order: EcommerceOrder, phone_digits: str, email: str) -> bool:
    if phone_digits and _digits(order.phone) == phone_digits:
        return True
    return bool(email and _clean_email(order.email) == email)

def _order_after_cutoff(order: EcommerceOrder, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    order_times = [
        _parse_datetime(order.shopify_created_at),
        _normalize_datetime(order.created_at),
    ]
    return any(value and value >= cutoff for value in order_times)

def _event_cutoff(event: AutomationEvent, payload: dict) -> datetime | None:
    for key in ("checkout_created_at", "created_at", "checkout_updated_at"):
        value = _parse_datetime(payload.get(key))
        if value:
            return value
    return _normalize_datetime(event.created_at)

def _order_is_cancelled(order: EcommerceOrder) -> bool:
    status_text = " ".join(
        str(value or "").lower()
        for value in [
            order.status,
            order.financial_status,
            order.fulfillment_status,
            order.delivery_status,
            order.raw_payload,
        ]
    )
    return any(marker in status_text for marker in ("cancelled", "canceled", "voided", "refunded"))

def _clean_email(value: Any) -> str:
    return str(value or "").strip().lower()

def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))

def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _normalize_datetime(datetime.fromisoformat(text))
    except ValueError:
        return None

def _normalize_datetime(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value

def process_due_automation_events(db: Session, limit: int = 50) -> dict:
    rows = db.execute(
        select(AutomationEvent)
        .where(
            AutomationEvent.status == "pending",
            AutomationEvent.scheduled_for <= _utcnow_naive(),
        )
        .order_by(AutomationEvent.scheduled_for.asc())
        .limit(max(1, min(limit, 200)))
    ).scalars().all()

    results = [process_automation_event(db, event) for event in rows]
    return {
        "status": "completed",
        "processed": len(results),
        "sent": sum(result.get("sent", 0) for result in results),
        "failed": sum(result.get("failed", 0) for result in results),
        "results": results,
    }

async def process_due_automation_events_with_session() -> dict:
    async with AsyncSessionLocal() as db:
        return await db.run_sync(
            lambda sync_db: process_due_automation_events(
                sync_db,
                settings.AUTOMATION_PROCESSOR_LIMIT,
            )
        )

async def automation_processor_loop() -> None:
    await asyncio.sleep(5)
    while settings.AUTOMATION_PROCESSOR_ENABLED:
        await process_due_automation_events_with_session()
        await asyncio.sleep(settings.AUTOMATION_PROCESSOR_INTERVAL_SECONDS)

__all__ = [
    "create_automation_event",
    "process_automation_event",
    "process_due_automation_events",
    "process_due_automation_events_with_session",
    "automation_processor_loop",
]
