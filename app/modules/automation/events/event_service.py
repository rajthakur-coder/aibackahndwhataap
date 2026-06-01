import json
from datetime import datetime, timedelta

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
            "cart_url": payload.get("cart_url") or "",
            "total": payload.get("total") or "",
            "currency": payload.get("currency") or "",
            "items": payload.get("items") or [],
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
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
        scheduled_for=datetime.utcnow() + timedelta(seconds=max(0, delay_seconds)),
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
    result = await db.execute(
        select(AutomationEvent)
        .where(
            AutomationEvent.tenant_id == normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
            AutomationEvent.status == "pending",
            AutomationEvent.scheduled_for <= datetime.utcnow(),
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
    if event.scheduled_for and event.scheduled_for > datetime.utcnow():
        return {"status": "skipped", "reason": "not_due"}

    payload = _load_json(event.payload, {})
    if not isinstance(payload, dict):
        payload = {}
    payload = {
        **payload,
        "external_id": event.external_id or payload.get("external_id") or "",
        "phone": event.phone or payload.get("phone") or "",
        "trigger": event.trigger,
        "source": event.source,
    }
    if not payload.get("cart_token"):
        payload["cart_token"] = payload.get("external_id") or str(payload.get("cart_url") or "").rstrip("/").rsplit("/", 1)[-1]

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
                AutomationExecution.event_id == event.id,
                AutomationExecution.rule_id == rule.id,
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        due_at = event.created_at + timedelta(seconds=max(0, rule.delay_seconds or 0))
        if due_at > datetime.utcnow():
            event.scheduled_for = due_at
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
            response = await _send_message(template, execution.phone, message, payload)
            execution.status = "sent"
            execution.provider_response = json.dumps(response, ensure_ascii=True)
            execution.sent_at = datetime.utcnow()
            db.add(
                Message(
                    tenant_id=normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
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
        event.processed_at = datetime.utcnow()
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
