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


from app.modules.automation.templates.template_service import *

async def create_automation_rule(db: AsyncSession, data: AutomationRuleRequest) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    if not data.name.strip() or not data.trigger.strip():
        return {"error": "Rule name and trigger are required", "status_code": 400}
    if not data.message_template_id and not (data.message_body or "").strip():
        return {"error": "Message template or message body is required", "status_code": 400}

    rule = AutomationRule(
        tenant_id=tenant_id,
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    statement = select(AutomationRule).where(AutomationRule.tenant_id == tenant_id).order_by(AutomationRule.created_at.desc())
    if trigger:
        statement = (
            select(AutomationRule)
            .where(AutomationRule.tenant_id == tenant_id, AutomationRule.trigger == trigger.strip())
            .order_by(AutomationRule.created_at.desc())
        )
    result = await db.execute(statement)
    return [
        {**serialize_rule(row), "sr_no": index}
        for index, row in enumerate(result.scalars().all(), start=1)
    ]

async def update_automation_rule(
    db: AsyncSession,
    rule_id: int,
    data: AutomationRuleUpdateRequest,
) -> dict:
    rule = await db.get(AutomationRule, rule_id)
    if not rule or rule.tenant_id != normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID):
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

def _conditions_match(rule: AutomationRule, payload: dict) -> bool:
    conditions = _load_json(rule.conditions, {})
    if not conditions:
        return True
    return all(_get_path(payload, key) == value for key, value in conditions.items())

async def _rule_template(db: AsyncSession, rule: AutomationRule) -> MessageTemplate | None:
    if not rule.message_template_id:
        return None
    template = await db.get(MessageTemplate, rule.message_template_id)
    if template and template.tenant_id == rule.tenant_id and template.status == "active":
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
            sync_automation._template_button_parameters(template, context),
        )
    return await run_in_threadpool(send_whatsapp_message, phone, message)

__all__ = [
    "create_automation_rule",
    "list_automation_rules",
    "update_automation_rule",
    "_conditions_match",
    "_rule_template",
    "_send_message",
]
