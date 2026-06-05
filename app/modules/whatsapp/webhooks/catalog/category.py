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
from app.modules.whatsapp.webhooks.processing.replies import (
    _queue_cross_sell_products,
    _queue_product_images,
    _send_product_carousel_reply,
    _send_product_list_reply,
    _send_text_reply,
)

from app.modules.whatsapp.webhooks.catalog.menu import *

async def _handle_catalog_category(context: WebhookProcessingContext) -> bool:
    selected_catalog_category = _selected_catalog_category(context.query_text)
    if not selected_catalog_category:
        return False

    category_page = _selected_catalog_product_page(context.query_text)
    page_size = max(context.requested_limit, 10)
    category_fetch_limit = page_size + 1
    category_offset = (category_page - 1) * page_size
    with context.timing.stage("shopify_fetch_category_products"):
        category_products_page = await _products_for_catalog_category(
            context.db,
            context.phone,
            selected_catalog_category,
            category_fetch_limit,
            offset=category_offset,
        )

    has_more_category_products = (
        selected_catalog_category != "best_sellers"
        and len(category_products_page) > page_size
    )
    category_products = category_products_page[:page_size]
    if not category_products:
        fallback_text = _localized(
            context.reply_language,
            "No products found in this category right now. You can try All products.",
            "Is category me abhi products nahi mile. Aap All products try kar sakte hain.",
        )
        await _send_text_reply(context, fallback_text)
        return True

    remember_last_products(context.db, context.phone, category_products)
    label = _catalog_category_label(selected_catalog_category, context.query_text)
    body_text = _localized(
        context.reply_language,
        f"You can browse {label} products.",
        f"{label} products dekh sakte hain.",
    )
    if await _send_product_carousel_reply(context, category_products, body_text, f"[carousel] {label}"):
        await _send_more_category_button_after_carousel_if_needed(
            context,
            selected_catalog_category,
            category_page,
            label,
            has_more_category_products,
        )
        return True
    if await _send_product_list_reply(context, category_products, label, body_text, f"[product_list] {label}"):
        await _send_more_category_button_if_needed(
            context,
            selected_catalog_category,
            category_page,
            label,
            has_more_category_products,
        )
        return True

    fallback_text = _localized(
        context.reply_language,
        "No products found in this category right now. You can try All products.",
        "Is category me abhi products nahi mile. Aap All products try kar sakte hain.",
    )
    await _send_text_reply(context, fallback_text)
    return True

def _catalog_category_label(selected_catalog_category: str, query_text: str | None = None) -> str:
    clicked_title = _catalog_category_clicked_title(selected_catalog_category, query_text)
    if clicked_title:
        return clicked_title
    label_key = selected_catalog_category.removeprefix("dynamic:")
    if label_key.startswith("collection_"):
        label_key = label_key.removeprefix("collection_")
    label_key = re.sub(r"_\d+$", "", label_key)
    return _CATALOG_CATEGORY_LABELS.get(
        selected_catalog_category,
        _CATALOG_CATEGORY_LABELS.get(label_key, label_key.replace("_", " ").title()),
    )

def _catalog_category_clicked_title(selected_catalog_category: str, query_text: str | None = None) -> str | None:
    normalized = " ".join((query_text or "").split())
    if not normalized:
        return None
    category_key = selected_catalog_category.removeprefix("dynamic:")
    match = re.search(rf"\bcatalog (?:dynamic )?category {re.escape(category_key)}\s+(.+)$", normalized, re.I)
    if not match:
        return None
    title = match.group(1).strip()
    if re.search(r"\bpage\s+\d+\b", title, re.I):
        return None
    return title or None

async def _send_more_category_button_if_needed(
    context: WebhookProcessingContext,
    selected_catalog_category: str,
    category_page: int,
    label: str,
    has_more_category_products: bool,
) -> None:
    if not has_more_category_products:
        return
    with context.timing.stage("whatsapp_send"):
        sent = await _try_send_category_more_button(
            context.phone,
            selected_catalog_category,
            category_page + 1,
            label,
            context.reply_language,
        )
    if sent:
        body = _localized(
            context.reply_language,
            f"Do you want to see more {label} products?",
            f"Aur {label} products dekhne hain?",
        )
        save_message(
            context.db,
            context.phone,
            f"[buttons] Show more {label}",
            "outgoing",
            message_type="buttons",
            payload={
                "body": body,
                "buttons": [{"title": "Show more"}],
            },
        )

async def _send_more_category_button_after_carousel_if_needed(
    context: WebhookProcessingContext,
    selected_catalog_category: str,
    category_page: int,
    label: str,
    has_more_category_products: bool,
) -> None:
    if not has_more_category_products:
        return
    await _send_more_category_button_if_needed(
        context,
        selected_catalog_category,
        category_page,
        label,
        has_more_category_products,
    )

__all__ = [
    "_handle_catalog_category",
    "_catalog_category_label",
    "_send_more_category_button_if_needed",
]
