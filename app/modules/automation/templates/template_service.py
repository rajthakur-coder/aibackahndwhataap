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
    return await db.run_sync(sync_automation.ensure_default_automation_rules)

async def create_message_template(db: AsyncSession, data: MessageTemplateRequest) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    if not data.name.strip() or not data.body.strip():
        return {"error": "Template name and body are required", "status_code": 400}

    result = await db.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == tenant_id, MessageTemplate.name == data.name.strip())
    )
    if result.scalar_one_or_none():
        return {"error": "Template name already exists", "status_code": 409}

    template = MessageTemplate(
        tenant_id=tenant_id,
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    result = await db.execute(
        select(MessageTemplate).where(MessageTemplate.tenant_id == tenant_id).order_by(MessageTemplate.created_at.desc())
    )
    return [serialize_template(row) for row in result.scalars().all()]

async def send_template_test(
    db: AsyncSession,
    template_id: int,
    data: SendTemplateRequest,
) -> dict:
    template = await db.get(MessageTemplate, template_id)
    if not template or template.tenant_id != normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID):
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
            sync_automation._template_button_parameters(template, data.context),
        )
    else:
        response = await run_in_threadpool(send_whatsapp_message, data.phone, rendered)

    return {"status": "sent", "rendered_message": rendered, "whatsapp": response}

__all__ = [
    "_load_json",
    "_get_path",
    "_template_parameters",
    "seed_default_automations",
    "create_message_template",
    "list_message_templates",
    "send_template_test",
]
