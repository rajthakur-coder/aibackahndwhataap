from __future__ import annotations

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
from app.models.whatsapp import WebhookEvent
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
from app.modules.ai.chat.openai_chat_service import generate_ai_reply
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
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.whatsapp.webhooks.processing.pipeline import WebhookProcessingContext

from app.modules.whatsapp.webhooks.processing.event_state import *
from app.modules.whatsapp.webhooks.processing.handoff_state import *

def _enqueue_understanding_log(
    phone: str,
    text: str,
    query_text: str,
    understanding,
    timing: WebhookTiming,
) -> None:
    with timing.stage("query_log_enqueue"):
        start_log_query_understanding(
            phone,
            {
                "message": text,
                "normalized_query": query_text,
                "intent": understanding.intent,
                "entities": understanding.entities,
                "confidence": understanding.confidence,
                "tool": understanding.tool,
                "source": understanding.source,
            },
        )

def _agent_state_for_message(
    db: Session,
    phone: str,
    query_text: str,
    understanding,
    timing: WebhookTiming,
    tenant_id: str | None = None,
) -> dict:
    if _should_skip_crm_agent_for_fast_path(query_text, understanding):
        with timing.stage("crm_agent_skipped"):
            return {
                "intent": understanding.intent,
                "reply_override": None,
                "context": "",
            }
    with timing.stage("crm_agent"):
        return process_agent_message(db, phone, query_text, tenant_id=tenant_id)

async def _apply_order_status_override(
    db: Session,
    phone: str,
    query_text: str,
    agent_state: dict,
    timing: WebhookTiming,
) -> None:
    if agent_state["intent"] != "order_status":
        return
    with timing.stage("shopify_order_fetch"):
        shopify_order_reply = await find_cached_order_status(db, phone, query_text)
    if shopify_order_reply:
        agent_state["reply_override"] = shopify_order_reply

def _reply_language_for_event(event: WebhookEvent, query_text: str, bot_settings) -> str:
    reply_language = _reply_language(query_text, bot_settings)
    explicit_language = str(getattr(bot_settings, "default_language", "english") or "english").strip().lower()
    if _is_internal_interactive_reply(event) and explicit_language not in {"hinglish", "hindi"}:
        return "english"
    return reply_language

def _within_business_hours(bot_settings) -> bool:
    if not bot_setting_enabled(bot_settings.business_hours_enabled):
        return True
    try:
        now = datetime.now(ZoneInfo(bot_settings.timezone or "Asia/Kolkata"))
        start_hour, start_minute = [int(part) for part in (bot_settings.business_hours_start or "09:00").split(":", 1)]
        end_hour, end_minute = [int(part) for part in (bot_settings.business_hours_end or "18:00").split(":", 1)]
    except Exception:
        return True
    current = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end

def _is_internal_interactive_reply(event: WebhookEvent) -> bool:
    try:
        payload = json.loads(event.payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    interactive = payload.get("interactive") or {}
    reply = {}
    if interactive.get("type") == "button_reply":
        reply = interactive.get("button_reply") or {}
    elif interactive.get("type") == "list_reply":
        reply = interactive.get("list_reply") or {}
    reply_id = str(reply.get("id") or "")
    return reply_id.startswith(("menu:", "catalog:"))

def _should_skip_crm_agent_for_fast_path(query_text: str, understanding) -> bool:
    if understanding.intent in {"greeting", "menu_request"} or _is_main_menu_request(query_text):
        return True
    if _is_catalog_page_request(query_text):
        return True
    if _selected_catalog_category(query_text):
        return True
    return _looks_like_catalog_request(query_text)

__all__ = [
    "_enqueue_understanding_log",
    "_agent_state_for_message",
    "_apply_order_status_override",
    "_reply_language_for_event",
    "_within_business_hours",
    "_is_internal_interactive_reply",
    "_should_skip_crm_agent_for_fast_path",
]
