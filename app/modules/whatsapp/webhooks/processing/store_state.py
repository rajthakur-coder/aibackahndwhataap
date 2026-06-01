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

def _active_store_bot_enabled(db: Session, phone: str | None = None) -> bool:
    phone_connection = _connection_for_phone(db, phone)
    if phone_connection:
        return bot_setting_enabled(phone_connection.bot_enabled)
    connection = db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.platform == "shopify", EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.desc())
    ).scalars().first()
    if not connection:
        return True
    return bot_setting_enabled(connection.bot_enabled)

def _connection_for_phone(db: Session, phone: str | None) -> EcommerceConnection | None:
    from app.modules.ecommerce.providers.shopify.connection_lookup_service import active_shopify_connection

    return active_shopify_connection(db, phone=phone)

def _tenant_for_phone(db: Session, phone: str | None) -> str:
    connection = _connection_for_phone(db, phone)
    return normalize_tenant_id(getattr(connection, "tenant_id", None) or DEFAULT_TENANT_ID)

__all__ = [
    "_active_store_bot_enabled",
    "_connection_for_phone",
    "_tenant_for_phone",
]
