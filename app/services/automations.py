import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.automation import AutomationEvent, AutomationExecution, AutomationRule, MessageTemplate
from app.models.ecommerce import EcommerceOrder
from app.models.entities import (
    AgentAction,
)
from app.services.messages import save_message
from app.services.whatsapp import send_whatsapp_message, send_whatsapp_template


TRIGGER_ORDER_CREATED = "order_created"
TRIGGER_ORDER_PAID = "order_paid"
TRIGGER_ORDER_SHIPPED = "order_shipped"
TRIGGER_ORDER_DELIVERED = "order_delivered"
TRIGGER_CART_ABANDONED = "cart_abandoned"
TRIGGER_COD_VERIFICATION = "cod_verification"
TRIGGER_FEEDBACK_REQUEST = "feedback_request"

VARIABLE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
TRUE_VALUES = {"1", "true", "yes", "on"}

DEFAULT_RULES = [
    {
        "name": "Order Confirmation",
        "trigger": TRIGGER_ORDER_CREATED,
        "message_body": (
            "Hi {{customer_name}}, your order {{order_number}} is confirmed. "
            "Total: {{total}} {{currency}}. We will update you when it ships."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "COD Verification",
        "trigger": TRIGGER_COD_VERIFICATION,
        "message_body": (
            "Hi {{customer_name}}, please reply YES to confirm your COD order "
            "{{order_number}} worth {{total}} {{currency}}."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Shipping Update",
        "trigger": TRIGGER_ORDER_SHIPPED,
        "message_body": (
            "Good news {{customer_name}}, your order {{order_number}} has shipped. "
            "Tracking: {{tracking_url}}"
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Delivered Follow-up",
        "trigger": TRIGGER_ORDER_DELIVERED,
        "message_body": (
            "Thank you {{customer_name}}! Your order {{order_number}} has been delivered. "
            "Reply with your feedback, or type YES to see matching recommendations."
        ),
        "delay_seconds": 0,
    },
    {
        "name": "Abandoned Cart Recovery",
        "trigger": TRIGGER_CART_ABANDONED,
        "message_body": (
            "Hi {{customer_name}}, you left items in your cart. Complete your order here: "
            "{{cart_url}}"
        ),
        "delay_seconds": 900,
    },
    {
        "name": "Feedback Request",
        "trigger": TRIGGER_FEEDBACK_REQUEST,
        "message_body": (
            "Hi {{customer_name}}, how was your experience with order {{order_number}}? "
            "Reply with a rating from 1 to 5."
        ),
        "delay_seconds": 86400,
    },
]


def bool_to_db(value: bool) -> str:
    return "true" if value else "false"


def db_to_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def ensure_default_automation_rules(db: Session) -> dict:
    created = 0
    for rule_data in DEFAULT_RULES:
        exists = (
            db.query(AutomationRule)
            .filter(AutomationRule.name == rule_data["name"])
            .first()
        )
        if exists:
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
    db.commit()
    return {"status": "success", "created": created}


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


def render_template(body: str, context: dict) -> str:
    def replace(match: re.Match) -> str:
        value = _get_path(context, match.group(1))
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True)
        return str(value)

    return VARIABLE_RE.sub(replace, body or "").strip()


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
    return {
        "id": rule.id,
        "name": rule.name,
        "trigger": rule.trigger,
        "message_template_id": rule.message_template_id,
        "message_body": rule.message_body,
        "delay_seconds": rule.delay_seconds,
        "conditions": _load_json(rule.conditions, {}),
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
        template = (
            db.query(MessageTemplate)
            .filter(MessageTemplate.id == rule.message_template_id)
            .first()
        )
        if template and template.status == "active":
            return template
    return None


def _rule_body(db: Session, rule: AutomationRule) -> str:
    template = _rule_template(db, rule)
    if template:
        return template.body
    return rule.message_body or ""


def _template_body_parameters(template: MessageTemplate, context: dict) -> list[str]:
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
        )
    return send_whatsapp_message(phone, rendered_message)


def _message_context(event: AutomationEvent) -> dict:
    payload = _load_json(event.payload, {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        **payload,
        "phone": event.phone or payload.get("phone") or "",
        "trigger": event.trigger,
        "source": event.source,
    }


def create_automation_event(
    db: Session,
    trigger: str,
    source: str,
    payload: dict,
    phone: str | None = None,
    external_id: str | None = None,
    delay_seconds: int = 0,
) -> AutomationEvent:
    delay_seconds = max(0, delay_seconds)
    if external_id:
        existing = (
            db.query(AutomationEvent)
            .filter(
                AutomationEvent.trigger == trigger.strip(),
                AutomationEvent.external_id == external_id,
            )
            .order_by(AutomationEvent.created_at.desc())
            .first()
        )
        if existing:
            return existing

    event = AutomationEvent(
        trigger=trigger.strip(),
        source=source.strip() or "system",
        external_id=external_id,
        phone=phone or payload.get("phone"),
        payload=json.dumps(payload, ensure_ascii=True),
        status="pending",
        scheduled_for=datetime.utcnow() + timedelta(seconds=delay_seconds),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def process_automation_event(db: Session, event: AutomationEvent) -> dict:
    if event.status == "processed":
        return {"status": "skipped", "reason": "already_processed"}
    if event.scheduled_for and event.scheduled_for > datetime.utcnow():
        return {"status": "skipped", "reason": "not_due"}

    payload = _message_context(event)
    rules = (
        db.query(AutomationRule)
        .filter(AutomationRule.trigger == event.trigger)
        .order_by(AutomationRule.created_at.asc())
        .all()
    )
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
        existing = (
            db.query(AutomationExecution)
            .filter(
                AutomationExecution.event_id == event.id,
                AutomationExecution.rule_id == rule.id,
            )
            .first()
        )
        if existing:
            skipped += 1
            continue

        due_at = event.created_at + timedelta(seconds=max(0, rule.delay_seconds or 0))
        if due_at > datetime.utcnow():
            event.scheduled_for = due_at
            pending_delayed += 1
            continue

        message = render_template(_rule_body(db, rule), payload)
        execution = AutomationExecution(
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
            execution.sent_at = datetime.utcnow()
            save_message(db, execution.phone, message, "outgoing")
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
        db.commit()

    event.processed_at = datetime.utcnow()
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


def process_due_automation_events(db: Session, limit: int = 50) -> dict:
    rows = (
        db.query(AutomationEvent)
        .filter(
            AutomationEvent.status == "pending",
            AutomationEvent.scheduled_for <= datetime.utcnow(),
        )
        .order_by(AutomationEvent.scheduled_for.asc())
        .limit(max(1, min(limit, 200)))
        .all()
    )

    results = [process_automation_event(db, event) for event in rows]
    return {
        "status": "completed",
        "processed": len(results),
        "sent": sum(result.get("sent", 0) for result in results),
        "failed": sum(result.get("failed", 0) for result in results),
        "results": results,
    }


def process_due_automation_events_with_session() -> dict:
    db = SessionLocal()
    try:
        return process_due_automation_events(db, settings.automation_processor_limit)
    finally:
        db.close()


async def automation_processor_loop() -> None:
    await asyncio.sleep(5)
    while settings.automation_processor_enabled:
        await run_in_threadpool(process_due_automation_events_with_session)
        await asyncio.sleep(settings.automation_processor_interval_seconds)


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
    if status_values & {"delivered"}:
        triggers.append(TRIGGER_ORDER_DELIVERED)
        triggers.append(TRIGGER_FEEDBACK_REQUEST)
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
