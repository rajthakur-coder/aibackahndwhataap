import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.automation import (
    AutomationEvent,
    AutomationExecution,
    AutomationRule,
    MessageTemplate,
)
from app.models.crm import AgentAction
from app.models.ecommerce import EcommerceOrder
from app.models.whatsapp import Message
from app.modules.automation.automation_schema import (
    AbandonedCartRequest,
    AutomationEventRequest,
    AutomationRuleRequest,
    AutomationRuleUpdateRequest,
    MessageTemplateRequest,
    SendTemplateRequest,
)
from app.modules.automation.runtime import sync_service as sync_automation
from app.modules.whatsapp.client.client_service import send_whatsapp_message, send_whatsapp_template
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


TRIGGER_ORDER_CREATED = sync_automation.TRIGGER_ORDER_CREATED
TRIGGER_ORDER_PAID = sync_automation.TRIGGER_ORDER_PAID
TRIGGER_ORDER_SHIPPED = sync_automation.TRIGGER_ORDER_SHIPPED
TRIGGER_ORDER_DELIVERED = sync_automation.TRIGGER_ORDER_DELIVERED
TRIGGER_CART_ABANDONED = sync_automation.TRIGGER_CART_ABANDONED
TRIGGER_COD_VERIFICATION = sync_automation.TRIGGER_COD_VERIFICATION
TRIGGER_FEEDBACK_REQUEST = sync_automation.TRIGGER_FEEDBACK_REQUEST

automation_processor_loop = sync_automation.automation_processor_loop
enqueue_order_automation_events = sync_automation.enqueue_order_automation_events
ensure_default_automation_rules = sync_automation.ensure_default_automation_rules
process_automation_event = sync_automation.process_automation_event
process_due_automation_events_with_session = sync_automation.process_due_automation_events_with_session
triggers_for_order = sync_automation.triggers_for_order

bool_to_db = sync_automation.bool_to_db
db_to_bool = sync_automation.db_to_bool
render_template = sync_automation.render_template
serialize_event = sync_automation.serialize_event
serialize_execution = sync_automation.serialize_execution
serialize_rule = sync_automation.serialize_rule
serialize_template = sync_automation.serialize_template


from app.modules.automation.rules.rule_service import *
from app.modules.automation.templates.template_service import *

async def create_automation_event(
    db: AsyncSession,
    data: AutomationEventRequest,
) -> dict:
    event = await _create_event(
        db,
        trigger=data.trigger,
        source=data.source,
        external_id=data.external_id,
        phone=data.phone,
        payload=data.payload,
        delay_seconds=data.delay_seconds,
    )
    return {"status": "queued", "event": serialize_event(event)}

async def create_abandoned_cart_event(db: AsyncSession, data: AbandonedCartRequest) -> dict:
    payload = data.model_dump()
    event = await _create_event(
        db,
        trigger=TRIGGER_CART_ABANDONED,
        source="api",
        external_id=payload.get("external_id"),
        phone=payload.get("phone"),
        payload={
            "customer_name": payload.get("customer_name") or "there",
            "phone": payload.get("phone"),
            "email": payload.get("email"),
            "cart_url": payload.get("cart_url") or "",
            "total": payload.get("total") or "",
            "currency": payload.get("currency") or "",
            "items": payload.get("items") or [],
            "checkout_created_at": payload.get("checkout_created_at"),
            "checkout_updated_at": payload.get("checkout_updated_at"),
        },
        delay_seconds=data.delay_seconds,
    )
    return {"status": "queued", "event": serialize_event(event)}

async def _create_event(
    db: AsyncSession,
    trigger: str,
    source: str,
    payload: dict,
    phone: str | None = None,
    external_id: str | None = None,
    delay_seconds: int = 0,
) -> AutomationEvent:
    tenant_id = normalize_tenant_id(payload.get("tenant_id") or current_tenant_id() or DEFAULT_TENANT_ID)
    if external_id:
        result = await db.execute(
            select(AutomationEvent)
            .where(
                AutomationEvent.tenant_id == tenant_id,
                AutomationEvent.trigger == trigger.strip(),
                AutomationEvent.external_id == external_id,
            )
            .order_by(AutomationEvent.created_at.desc())
        )
        existing = result.scalars().first()
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
        scheduled_for=_utcnow_naive() + timedelta(seconds=max(0, delay_seconds)),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event

async def list_automation_events(
    db: AsyncSession,
    status: str | None = None,
    trigger: str | None = None,
) -> list[dict]:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    statement = select(AutomationEvent).where(AutomationEvent.tenant_id == tenant_id)
    if status:
        statement = statement.where(AutomationEvent.status == status.strip())
    if trigger:
        statement = statement.where(AutomationEvent.trigger == trigger.strip())
    statement = statement.order_by(AutomationEvent.created_at.desc()).limit(200)
    result = await db.execute(statement)
    return [serialize_event(row) for row in result.scalars().all()]

async def process_event(db: AsyncSession, event_id: int) -> dict:
    event = await db.get(AutomationEvent, event_id)
    if not event or event.tenant_id != normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID):
        return {"error": "Automation event not found", "status_code": 404}
    return await _process_event(db, event)

async def process_due_events(db: AsyncSession, limit: int = 50) -> dict:
    now = _utcnow_naive()
    result = await db.execute(
        select(AutomationEvent)
        .where(
            AutomationEvent.tenant_id == normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
            AutomationEvent.status == "pending",
            AutomationEvent.scheduled_for <= now,
        )
        .order_by(AutomationEvent.scheduled_for.asc())
        .limit(max(1, min(limit, 200)))
    )
    rows = result.scalars().all()
    results = [await _process_event(db, event) for event in rows]
    return {
        "status": "completed",
        "processed": len(results),
        "sent": sum(item.get("sent", 0) for item in results),
        "failed": sum(item.get("failed", 0) for item in results),
        "results": results,
    }

async def list_automation_executions(
    db: AsyncSession,
    status: str | None = None,
) -> list[dict]:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    statement = select(AutomationExecution).where(AutomationExecution.tenant_id == tenant_id)
    if status:
        statement = statement.where(AutomationExecution.status == status.strip())
    statement = statement.order_by(AutomationExecution.created_at.desc()).limit(200)
    result = await db.execute(statement)
    return [serialize_execution(row) for row in result.scalars().all()]

async def _process_event(db: AsyncSession, event: AutomationEvent) -> dict:
    if event.status == "processed":
        return {"status": "skipped", "reason": "already_processed"}
    if event.scheduled_for and event.scheduled_for > _utcnow_like(event.scheduled_for):
        return {"status": "skipped", "reason": "not_due"}

    payload = sync_automation._message_context(event)

    if await _abandoned_cart_was_converted(db, event, payload):
        event.status = "processed"
        event.processed_at = _utcnow_naive()
        event.error = "Skipped abandoned cart reminder because this customer placed an order."
        await db.commit()
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

    result = await db.execute(
        select(AutomationRule)
        .where(AutomationRule.tenant_id == event.tenant_id, AutomationRule.trigger == event.trigger)
        .order_by(AutomationRule.created_at.asc())
    )
    rules = [
        rule
        for rule in result.scalars().all()
        if db_to_bool(rule.enabled) and _conditions_match(rule, payload)
    ]

    sent = 0
    failed = 0
    skipped = 0
    pending_delayed = 0
    errors = []
    event.status = "processing"
    event.error = None
    await db.commit()

    for rule in rules:
        existing = await db.execute(
            select(AutomationExecution).where(
                AutomationExecution.tenant_id == event.tenant_id,
                AutomationExecution.event_id == event.id,
                AutomationExecution.rule_id == rule.id,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        due_at = event.created_at + timedelta(seconds=max(0, rule.delay_seconds or 0))
        if due_at > _utcnow_like(due_at):
            event.scheduled_for = _db_naive(due_at)
            pending_delayed += 1
            continue

        template = await _rule_template(db, rule)
        message = render_template(template.body if template else rule.message_body or "", payload)
        execution = AutomationExecution(
            tenant_id=event.tenant_id,
            event_id=event.id,
            rule_id=rule.id,
            phone=event.phone or payload.get("phone"),
            status="pending",
            rendered_message=message,
        )
        db.add(execution)
        await db.commit()
        await db.refresh(execution)

        if not execution.phone or not message:
            execution.status = "skipped"
            execution.error = "Missing phone or rendered message"
            skipped += 1
            await db.commit()
            continue

        try:
            response = await _send_message(template, execution.phone, message, payload, event.tenant_id)
            execution.status = "sent"
            execution.provider_response = json.dumps(response, ensure_ascii=True)
            execution.sent_at = _utcnow_naive()
            db.add(
                Message(
                    tenant_id=event.tenant_id,
                    phone=execution.phone,
                    message=message,
                    direction="outgoing",
                )
            )
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
        await db.commit()

    if pending_delayed:
        event.status = "pending"
        event.processed_at = None
    else:
        event.status = "failed" if failed and not sent else "processed"
    event.processed_at = _utcnow_naive()
    event.error = json.dumps(errors[:5], ensure_ascii=True) if errors else None
    await db.commit()
    return {
        "status": event.status,
        "event_id": event.id,
        "matched_rules": len(rules),
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "pending_delayed": pending_delayed,
        "errors": errors[:5],
    }

async def _abandoned_cart_was_converted(db: AsyncSession, event: AutomationEvent, payload: dict) -> bool:
    if event.trigger != TRIGGER_CART_ABANDONED:
        return False

    phone_digits = _digits(event.phone or payload.get("phone"))
    email = _clean_email(payload.get("email"))
    if not phone_digits and not email:
        return False

    cutoff = _event_cutoff(event, payload)
    result = await db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.tenant_id == event.tenant_id)
        .order_by(EcommerceOrder.updated_at.desc(), EcommerceOrder.id.desc())
        .limit(300)
    )
    for order in result.scalars().all():
        if not _same_customer_order(order, phone_digits, email):
            continue
        if not _order_after_cutoff(order, cutoff):
            continue
        if _order_is_cancelled(order):
            continue
        return True
    return False


async def _send_message(
    template: MessageTemplate | None,
    phone: str,
    message: str,
    context: dict,
    tenant_id: str,
) -> dict:
    if template and template.template_type == "whatsapp_template":
        return await run_in_threadpool(
            send_whatsapp_template,
            phone,
            template.provider_template_name or template.name,
            template.language or "en",
            sync_automation._template_body_parameters(template, context),
            sync_automation._template_button_parameters(template, context),
            tenant_id,
        )
    return await run_in_threadpool(send_whatsapp_message, phone, message, tenant_id)

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


def _utcnow_like(value: datetime | None = None) -> datetime:
    now = datetime.now(timezone.utc)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return now
    return now.replace(tzinfo=None)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _db_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value

__all__ = [
    "create_automation_event",
    "create_abandoned_cart_event",
    "_create_event",
    "list_automation_events",
    "process_event",
    "process_due_events",
    "list_automation_executions",
    "_process_event",
]
