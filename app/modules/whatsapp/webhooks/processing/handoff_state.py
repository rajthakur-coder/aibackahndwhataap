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
from app.modules.whatsapp.webhooks.processing.store_state import _active_store_bot_enabled

def _skip_auto_reply_if_disabled(
    db: Session,
    phone: str,
    text: str,
    bot_settings,
) -> bool:
    if not bot_setting_enabled(bot_settings.bot_enabled):
        _log_skipped_auto_reply(db, phone, "bot_disabled_auto_reply_skipped", text)
        return True
    if not _active_store_bot_enabled(db, phone):
        _log_skipped_auto_reply(db, phone, "store_bot_disabled_auto_reply_skipped", text)
        return True
    return False

def _log_skipped_auto_reply(db: Session, phone: str, action_type: str, text: str) -> None:
    db.add(
        AgentAction(
            phone=phone,
            action_type=action_type,
            status="skipped",
            payload=json.dumps({"message": text}),
        )
    )
    db.commit()

def _within_business_hours(bot_settings) -> bool:
    if not bot_setting_enabled(bot_settings.business_hours_enabled):
        return True
    try:
        now = datetime.now(ZoneInfo(bot_settings.timezone or "Asia/Kolkata"))
        start_hour, start_minute = [
            int(part)
            for part in (bot_settings.business_hours_start or "09:00").split(":", 1)
        ]
        end_hour, end_minute = [
            int(part)
            for part in (bot_settings.business_hours_end or "18:00").split(":", 1)
        ]
    except Exception:
        return True

    current = now.hour * 60 + now.minute
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end

async def _handle_active_handoff(
    db: Session,
    event: WebhookEvent,
    phone: str,
    text: str,
    bot_settings,
    timing: WebhookTiming,
) -> bool:
    with timing.stage("handoff_check"):
        active_handoff = _active_handoff_ticket(db, phone, event.tenant_id)
    if not active_handoff:
        return False

    _append_handoff_summary(db, active_handoff, "incoming", text)
    handoff_text = _localized(
        _reply_language(text, bot_settings),
        f"Your request is already with our support team. Ticket #{active_handoff.id} is open, and they will reply shortly.",
        f"Aapki request support team ke paas hai. Ticket #{active_handoff.id} open hai, team jaldi reply karegi.",
    )
    with timing.stage("whatsapp_send"):
        await run_in_threadpool(send_whatsapp_message, phone, handoff_text)
    save_message(db, phone, handoff_text, "outgoing")
    db.add(
        AgentAction(
            phone=phone,
            action_type="handoff_message_received",
            status="open",
            payload=json.dumps({"ticket_id": active_handoff.id, "message": text}),
        )
    )
    db.commit()
    return True

async def _handle_offline_handoff(
    db: Session,
    phone: str,
    bot_settings,
    agent_state: dict,
    timing: WebhookTiming,
) -> bool:
    if agent_state["intent"] != "human_handoff" or _within_business_hours(bot_settings):
        return False
    offline_text = bot_settings.offline_message or (
        "Our support team is offline right now. Your request is noted and the team will reply during business hours."
    )
    with timing.stage("whatsapp_send"):
        await run_in_threadpool(send_whatsapp_message, phone, offline_text)
    save_message(db, phone, offline_text, "outgoing")
    return True

def _active_handoff_ticket(db: Session, phone: str, tenant_id: str | None = None) -> HandoffTicket | None:
    if not phone:
        return None
    tenant_id = normalize_tenant_id(tenant_id or DEFAULT_TENANT_ID)
    return db.execute(
        select(HandoffTicket)
        .where(HandoffTicket.tenant_id == tenant_id, HandoffTicket.phone == phone, HandoffTicket.status == "open")
        .order_by(HandoffTicket.updated_at.desc())
    ).scalars().first()

def _append_handoff_summary(db: Session, ticket: HandoffTicket, direction: str, message: str) -> None:
    line = f"{direction}: {message}".strip()
    ticket.summary = "\n".join(filter(None, [ticket.summary, line]))[-5000:]
    ticket.updated_at = datetime.utcnow()
    db.commit()

__all__ = [
    "_skip_auto_reply_if_disabled",
    "_log_skipped_auto_reply",
    "_handle_active_handoff",
    "_handle_offline_handoff",
    "_within_business_hours",
    "_active_handoff_ticket",
    "_append_handoff_summary",
]
