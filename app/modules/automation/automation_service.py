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
from app.models.entities import AgentAction, Message
from app.modules.automation.automation_schema import (
    AbandonedCartRequest,
    AutomationEventRequest,
    AutomationRuleRequest,
    AutomationRuleUpdateRequest,
    MessageTemplateRequest,
    SendTemplateRequest,
)
from app.services import automations as sync_automation
from app.services.whatsapp import send_whatsapp_message, send_whatsapp_template


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


def _load_json(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _get_path(data: dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _template_parameters(template: MessageTemplate, context: dict) -> list[str]:
    variable_order = _load_json(template.body_variable_order, [])
    if not isinstance(variable_order, list):
        return []
    values = []
    for variable in variable_order:
        if not isinstance(variable, str):
            continue
        value = _get_path(context, variable)
        if value is None:
            values.append("")
        elif isinstance(value, (dict, list)):
            values.append(json.dumps(value, ensure_ascii=True))
        else:
            values.append(str(value))
    return values


async def seed_default_automations(db: AsyncSession) -> dict:
    created = 0
    for rule_data in sync_automation.DEFAULT_RULES:
        result = await db.execute(
            select(AutomationRule).where(AutomationRule.name == rule_data["name"])
        )
        if result.scalar_one_or_none():
            continue
        db.add(
            AutomationRule(
                name=rule_data["name"],
                trigger=rule_data["trigger"],
                message_body=rule_data["message_body"],
                delay_seconds=rule_data["delay_seconds"],
                enabled="true",
            )
        )
        created += 1
    await db.commit()
    return {"status": "success", "created": created}


async def create_message_template(db: AsyncSession, data: MessageTemplateRequest) -> dict:
    if not data.name.strip() or not data.body.strip():
        return {"error": "Template name and body are required", "status_code": 400}

    result = await db.execute(
        select(MessageTemplate).where(MessageTemplate.name == data.name.strip())
    )
    if result.scalar_one_or_none():
        return {"error": "Template name already exists", "status_code": 409}

    template = MessageTemplate(
        name=data.name.strip(),
        body=data.body.strip(),
        channel=data.channel.strip() or "whatsapp",
        template_type=data.template_type.strip() or "text",
        provider_template_name=(
            data.provider_template_name.strip()
            if data.provider_template_name
            else None
        ),
        language=data.language.strip() or "en",
        body_variable_order=json.dumps(data.body_variable_order, ensure_ascii=True),
        status=data.status.strip() or "active",
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return {"status": "success", "template": serialize_template(template)}


async def list_message_templates(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(MessageTemplate).order_by(MessageTemplate.created_at.desc())
    )
    return [serialize_template(row) for row in result.scalars().all()]


async def send_template_test(
    db: AsyncSession,
    template_id: int,
    data: SendTemplateRequest,
) -> dict:
    template = await db.get(MessageTemplate, template_id)
    if not template:
        return {"error": "Message template not found", "status_code": 404}
    if template.status != "active":
        return {"error": "Message template is not active", "status_code": 400}

    rendered = render_template(template.body, data.context)
    if template.template_type == "whatsapp_template":
        response = await run_in_threadpool(
            send_whatsapp_template,
            data.phone,
            template.provider_template_name or template.name,
            template.language or "en",
            _template_parameters(template, data.context),
        )
    else:
        response = await run_in_threadpool(send_whatsapp_message, data.phone, rendered)

    return {"status": "sent", "rendered_message": rendered, "whatsapp": response}


async def create_automation_rule(db: AsyncSession, data: AutomationRuleRequest) -> dict:
    if not data.name.strip() or not data.trigger.strip():
        return {"error": "Rule name and trigger are required", "status_code": 400}
    if not data.message_template_id and not (data.message_body or "").strip():
        return {"error": "Message template or message body is required", "status_code": 400}

    rule = AutomationRule(
        name=data.name.strip(),
        trigger=data.trigger.strip(),
        message_template_id=data.message_template_id,
        message_body=(data.message_body or "").strip() or None,
        delay_seconds=max(0, data.delay_seconds),
        conditions=json.dumps(data.conditions or {}, ensure_ascii=True),
        enabled=bool_to_db(data.enabled),
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return {"status": "success", "rule": serialize_rule(rule)}


async def list_automation_rules(db: AsyncSession, trigger: str | None = None) -> list[dict]:
    statement = select(AutomationRule).order_by(AutomationRule.created_at.desc())
    if trigger:
        statement = (
            select(AutomationRule)
            .where(AutomationRule.trigger == trigger.strip())
            .order_by(AutomationRule.created_at.desc())
        )
    result = await db.execute(statement)
    return [serialize_rule(row) for row in result.scalars().all()]


async def update_automation_rule(
    db: AsyncSession,
    rule_id: int,
    data: AutomationRuleUpdateRequest,
) -> dict:
    rule = await db.get(AutomationRule, rule_id)
    if not rule:
        return {"error": "Automation rule not found", "status_code": 404}

    if data.name is not None:
        rule.name = data.name.strip() or rule.name
    if data.trigger is not None:
        rule.trigger = data.trigger.strip() or rule.trigger
    if data.message_template_id is not None:
        rule.message_template_id = data.message_template_id
    if data.message_body is not None:
        rule.message_body = data.message_body.strip() or None
    if data.delay_seconds is not None:
        rule.delay_seconds = max(0, data.delay_seconds)
    if data.conditions is not None:
        rule.conditions = json.dumps(data.conditions, ensure_ascii=True)
    if data.enabled is not None:
        rule.enabled = bool_to_db(data.enabled)

    await db.commit()
    await db.refresh(rule)
    return {"status": "success", "rule": serialize_rule(rule)}


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
    if external_id:
        result = await db.execute(
            select(AutomationEvent)
            .where(
                AutomationEvent.trigger == trigger.strip(),
                AutomationEvent.external_id == external_id,
            )
            .order_by(AutomationEvent.created_at.desc())
        )
        existing = result.scalars().first()
        if existing:
            return existing

    event = AutomationEvent(
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
    statement = select(AutomationEvent)
    if status:
        statement = statement.where(AutomationEvent.status == status.strip())
    if trigger:
        statement = statement.where(AutomationEvent.trigger == trigger.strip())
    statement = statement.order_by(AutomationEvent.created_at.desc()).limit(200)
    result = await db.execute(statement)
    return [serialize_event(row) for row in result.scalars().all()]


async def process_event(db: AsyncSession, event_id: int) -> dict:
    event = await db.get(AutomationEvent, event_id)
    if not event:
        return {"error": "Automation event not found", "status_code": 404}
    return await _process_event(db, event)


async def process_due_events(db: AsyncSession, limit: int = 50) -> dict:
    result = await db.execute(
        select(AutomationEvent)
        .where(
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
    statement = select(AutomationExecution)
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
        "phone": event.phone or payload.get("phone") or "",
        "trigger": event.trigger,
        "source": event.source,
    }

    result = await db.execute(
        select(AutomationRule)
        .where(AutomationRule.trigger == event.trigger)
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
            db.add(Message(phone=execution.phone, message=message, direction="outgoing"))
            sent += 1
        except Exception as exc:
            execution.status = "failed"
            execution.error = str(exc)
            db.add(
                AgentAction(
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


def _conditions_match(rule: AutomationRule, payload: dict) -> bool:
    conditions = _load_json(rule.conditions, {})
    if not conditions:
        return True
    return all(_get_path(payload, key) == value for key, value in conditions.items())


async def _rule_template(db: AsyncSession, rule: AutomationRule) -> MessageTemplate | None:
    if not rule.message_template_id:
        return None
    template = await db.get(MessageTemplate, rule.message_template_id)
    if template and template.status == "active":
        return template
    return None


async def _send_message(
    template: MessageTemplate | None,
    phone: str,
    message: str,
    context: dict,
) -> dict:
    if template and template.template_type == "whatsapp_template":
        return await run_in_threadpool(
            send_whatsapp_template,
            phone,
            template.provider_template_name or template.name,
            template.language or "en",
            _template_parameters(template, context),
        )
    return await run_in_threadpool(send_whatsapp_message, phone, message)
