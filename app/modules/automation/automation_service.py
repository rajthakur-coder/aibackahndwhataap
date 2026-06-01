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


TRIGGER_ORDER_CREATED = sync_automation.TRIGGER_ORDER_CREATED
TRIGGER_ORDER_PAID = sync_automation.TRIGGER_ORDER_PAID
TRIGGER_ORDER_SHIPPED = sync_automation.TRIGGER_ORDER_SHIPPED
TRIGGER_ORDER_DELIVERED = sync_automation.TRIGGER_ORDER_DELIVERED
TRIGGER_CART_ABANDONED = sync_automation.TRIGGER_CART_ABANDONED
TRIGGER_COD_VERIFICATION = sync_automation.TRIGGER_COD_VERIFICATION
TRIGGER_FEEDBACK_REQUEST = sync_automation.TRIGGER_FEEDBACK_REQUEST
TRIGGER_POST_DISPATCH_CROSS_SELL = sync_automation.TRIGGER_POST_DISPATCH_CROSS_SELL
TRIGGER_DELIVERED_REVIEW = sync_automation.TRIGGER_DELIVERED_REVIEW
TRIGGER_REPLENISHMENT = sync_automation.TRIGGER_REPLENISHMENT
TRIGGER_BROWSE_NO_BUY = sync_automation.TRIGGER_BROWSE_NO_BUY

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


from app.modules.automation.events.event_service import *
from app.modules.automation.rules.rule_service import *
from app.modules.automation.templates.template_service import *
