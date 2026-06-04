import json
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction, HandoffTicket
from app.models.ecommerce import EcommerceConnection
from app.models.whatsapp import WebhookEvent, WhatsappCredential
from app.modules.crm.agent.agent_service import bot_setting_enabled, get_bot_settings, process_agent_message
from app.modules.ai.tools.ai_tools_service import ToolDecision, decide_tool_for_message, run_ai_tool
from app.modules.crm.memory.conversation_memory_service import remember_last_products
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.live_chat.live_chat_service import serialize_message
from app.modules.whatsapp.live_chat.socket import live_chat_manager
from app.modules.whatsapp.analytics.analytics_service import tracking_url
from app.modules.whatsapp.webhooks.tasks.background_service import (
    start_log_interactive_click,
    start_log_query_understanding,
    start_mark_read_with_typing,
    start_remember_last_question,
)
from app.modules.whatsapp.webhooks.observability.timing_service import WebhookTiming
from app.modules.whatsapp.webhooks.responses.response_service import (
    CATALOG_CATEGORY_LABELS as _CATALOG_CATEGORY_LABELS,
    catalog_page_number as _catalog_page_number,
    is_catalog_page_request as _is_catalog_page_request,
    is_main_menu_request as _is_main_menu_request,
    localized as _localized,
    looks_like_catalog_request as _looks_like_catalog_request,
    products_for_catalog_category as _products_for_catalog_category,
    reply_language as _reply_language,
    requested_limit_from_understanding as _requested_limit_from_understanding,
    selected_catalog_category as _selected_catalog_category,
    selected_catalog_product_page as _selected_catalog_product_page,
    try_send_catalog_category_list as _try_send_catalog_category_list,
    try_send_category_more_button as _try_send_category_more_button,
    try_send_main_menu as _try_send_main_menu,
    try_send_product_carousel as _try_send_product_carousel,
    try_send_product_cta as _try_send_product_cta,
    try_send_product_list as _try_send_product_list,
    understanding_context as _understanding_context,
)
from app.shared.arq_queue import enqueue_whatsapp_cross_sell, enqueue_whatsapp_product_images
from app.modules.ai.understanding.query_understanding_service import understand_message
from app.modules.ai.recommendations.sales_recommendations_service import (
    is_top_selling_request,
    recommendation_intro,
)
from app.modules.ecommerce.catalog.catalog_cache_service import (
    find_cached_catalog_products,
    find_cached_order_status,
    find_cached_product_image,
    find_cached_product_recommendations,
    find_cached_top_selling_products,
)
from app.modules.whatsapp.client.client_service import (
    send_whatsapp_image,
    send_whatsapp_message,
)
from app.modules.whatsapp.webhooks.events.event_service import resolve_whatsapp_webhook_tenant_id
from app.modules.whatsapp.webhooks.processing.event_state import _incoming_display_text
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id, set_current_tenant_id


@dataclass
class WebhookProcessingContext:
    event: WebhookEvent
    db: Session
    phone: str
    text: str
    timing: WebhookTiming
    tenant_id: str
    bot_settings: object
    understanding: object
    query_text: str
    agent_state: dict
    requested_limit: int
    reply_language: str
from app.modules.whatsapp.webhooks.processing.catalog import *
from app.modules.whatsapp.webhooks.flows.commerce_flows import *
from app.modules.whatsapp.webhooks.processing.replies import *
from app.modules.whatsapp.webhooks.processing.state import *



async def process_webhook_event(event: WebhookEvent, db: Session) -> None:
    phone = event.phone or ""
    text = event.message_text or ""
    timing = WebhookTiming(db, phone, event.id)
    attempt_number = _start_processing_event(db, event, timing)

    if attempt_number == 1:
        await _persist_incoming_message(db, event, phone, text, timing)

    text = _incoming_display_text(event, text)

    tenant_id = _resolve_webhook_tenant_id(db, event, phone)
    set_current_tenant_id(tenant_id)
    if event.tenant_id != tenant_id:
        event.tenant_id = tenant_id
        db.commit()
    with timing.stage("bot_settings"):
        bot_settings = get_bot_settings(db, tenant_id=tenant_id)

    if _skip_auto_reply_if_disabled(db, phone, text, bot_settings):
        _mark_processed(db, event, timing)
        return

    with timing.stage("whatsapp_typing"):
        typing_handle = start_mark_read_with_typing(event)

    try:
        await _process_webhook_event_with_typing(event, db, phone, text, timing, tenant_id, bot_settings)
    finally:
        if typing_handle:
            typing_handle.stop()


async def _process_webhook_event_with_typing(
    event: WebhookEvent,
    db: Session,
    phone: str,
    text: str,
    timing: WebhookTiming,
    tenant_id: str,
    bot_settings: object,
) -> None:

    if await _handle_active_handoff(db, event, phone, text, bot_settings, timing):
        _mark_processed(db, event, timing)
        return

    with timing.stage("intent"):
        understanding = understand_message(text)
    query_text = understanding.normalized_query or text
    _enqueue_understanding_log(phone, text, query_text, understanding, timing)

    requested_limit = _requested_limit_from_understanding(understanding, query_text)
    reply_language = _reply_language_for_event(event, query_text, bot_settings)
    pre_agent_context = WebhookProcessingContext(
        event=event,
        db=db,
        phone=phone,
        text=text,
        timing=timing,
        tenant_id=tenant_id,
        bot_settings=bot_settings,
        understanding=understanding,
        query_text=query_text,
        agent_state={},
        requested_limit=requested_limit,
        reply_language=reply_language,
    )
    if await _handle_commerce_interactive_flows(pre_agent_context):
        _mark_processed(db, event, timing)
        return

    agent_state = _agent_state_for_message(db, phone, query_text, understanding, timing, tenant_id=tenant_id)
    if await _handle_offline_handoff(db, phone, bot_settings, agent_state, timing):
        _mark_processed(db, event, timing)
        return
    await _apply_order_status_override(db, phone, query_text, agent_state, timing)

    context = WebhookProcessingContext(
        event=event,
        db=db,
        phone=phone,
        text=text,
        timing=timing,
        tenant_id=tenant_id,
        bot_settings=bot_settings,
        understanding=understanding,
        query_text=query_text,
        agent_state=agent_state,
        requested_limit=requested_limit,
        reply_language=reply_language,
    )

    handlers = (
        _handle_main_menu,
        _handle_catalog_page,
        _handle_catalog_category,
        _handle_catalog_request,
        _handle_top_selling_products,
        _handle_recommended_products,
        _handle_catalog_products,
        _handle_product_image,
    )
    for handler in handlers:
        if await handler(context):
            _mark_processed(db, event, timing)
            return

    await _send_ai_reply(context)
    _mark_processed(db, event, timing)


def _resolve_webhook_tenant_id(db: Session, event: WebhookEvent, phone: str) -> str:
    tenant_id = normalize_tenant_id(event.tenant_id or "")
    if tenant_id != DEFAULT_TENANT_ID:
        return tenant_id

    metadata = _event_metadata(event)
    resolved = resolve_whatsapp_webhook_tenant_id(
        db,
        {
            "phone": phone,
            "phone_number_id": metadata.get("phone_number_id"),
            "display_phone_number": metadata.get("display_phone_number"),
            "waba_id": metadata.get("waba_id"),
        },
    )
    if resolved:
        return resolved

    raise RuntimeError("Cannot process WhatsApp webhook without a resolved non-default tenant")


def _event_metadata(event: WebhookEvent) -> dict:
    try:
        payload = json.loads(event.payload or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}















































































