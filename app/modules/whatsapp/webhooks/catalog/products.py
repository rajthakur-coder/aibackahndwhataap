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

from app.modules.whatsapp.webhooks.catalog.category import *

async def _handle_top_selling_products(context: WebhookProcessingContext) -> bool:
    if not is_top_selling_request(context.query_text) and context.understanding.intent != "top_selling_products":
        return False
    with context.timing.stage("shopify_top_selling_fetch"):
        top_selling_products = await find_cached_top_selling_products(
            context.db,
            limit=context.requested_limit,
            phone=context.phone,
        )
    if not top_selling_products:
        recommendation_text = _localized(
            context.reply_language,
            "Sales data is not available yet to calculate top-selling products.",
            "Abhi top-selling products nikalne ke liye order/sales data available nahi hai.",
        )
        await _send_text_reply(context, recommendation_text)
        return True

    remember_last_products(context.db, context.phone, top_selling_products)
    body_text = _localized(
        context.reply_language,
        "These are the top-selling products.",
        "Ye top-selling products hain.",
    )
    if await _send_product_carousel_reply(context, top_selling_products, body_text, "[carousel] Top selling products"):
        return True
    if await _send_product_list_reply(
        context,
        top_selling_products,
        "Top selling products",
        body_text,
        "[product_list] Top selling products",
    ):
        return True

    recommendation_text = recommendation_intro(context.text, top_selling_products)
    await _send_text_reply(context, recommendation_text)
    await _queue_product_images(
        context.db,
        context.phone,
        top_selling_products,
        caption_mode="recommendation",
        failure_action="top_selling_image_send_failed",
    )
    return True

async def _handle_recommended_products(context: WebhookProcessingContext) -> bool:
    with context.timing.stage("shopify_recommendations_fetch"):
        recommended_products = await find_cached_product_recommendations(
            context.db,
            context.query_text,
            limit=context.requested_limit,
            entities=context.understanding.entities,
            phone=context.phone,
        )
    if not recommended_products:
        return False

    remember_last_products(context.db, context.phone, recommended_products)
    body_text = _localized(context.reply_language, "Matching products for you.", "Aapke liye matching products.")
    if await _send_product_carousel_reply(context, recommended_products, body_text, "[carousel] Recommended products"):
        await _queue_cross_sell_products(context.db, context.phone, context.query_text, recommended_products)
        return True
    if not await _send_product_list_reply(
        context,
        recommended_products,
        "Recommended products",
        body_text,
        "[product_list] Recommended products",
    ):
        recommendation_text = recommendation_intro(context.text, recommended_products)
        await _send_text_reply(context, recommendation_text)
        await _queue_product_images(
            context.db,
            context.phone,
            recommended_products,
            caption_mode="recommendation",
            failure_action="recommendation_image_send_failed",
        )

    await _queue_cross_sell_products(context.db, context.phone, context.query_text, recommended_products)
    return True

async def _handle_catalog_products(context: WebhookProcessingContext) -> bool:
    with context.timing.stage("shopify_catalog_fetch"):
        catalog_products = await find_cached_catalog_products(
            context.db,
            context.query_text,
            limit=context.requested_limit,
            entities=context.understanding.entities,
            phone=context.phone,
        )
    catalog_products = catalog_products or []
    if not catalog_products:
        return False

    remember_last_products(context.db, context.phone, catalog_products)
    body_text = _localized(
        context.reply_language,
        "You can browse these catalog products.",
        "Catalog products dekh sakte hain.",
    )
    if await _send_product_carousel_reply(context, catalog_products, body_text, "[carousel] Catalog"):
        await _queue_cross_sell_products(context.db, context.phone, context.query_text, catalog_products)
        return True
    if await _send_product_list_reply(context, catalog_products, "Catalog", body_text, "[product_list] Catalog"):
        await _queue_cross_sell_products(context.db, context.phone, context.query_text, catalog_products)
        return True

    catalog_text = _catalog_products_text(context.phone, catalog_products)
    await _send_text_reply(context, catalog_text)
    await _queue_product_images(
        context.db,
        context.phone,
        catalog_products,
        caption_mode="caption",
        failure_action="catalog_image_send_failed",
    )
    await _queue_cross_sell_products(context.db, context.phone, context.query_text, catalog_products)
    return True

def _catalog_products_text(phone: str, catalog_products: list[dict]) -> str:
    lines = ["Catalog:"]
    for index, product in enumerate(catalog_products, start=1):
        price = product.get("price_min") or ""
        if product.get("price_max") and product["price_max"] != product.get("price_min"):
            price = f"{product.get('price_min') or ''} - {product['price_max']}"
        product_line = f"{index}. {product['title']}"
        if price:
            product_line += f" - {price}"
        if product.get("product_url"):
            product_line += "\n" + tracking_url(
                product["product_url"],
                phone=phone,
                source="catalog_text",
                title=product["title"],
            )
        lines.append(product_line)
    return "\n\n".join(lines)

__all__ = [
    "_handle_top_selling_products",
    "_handle_recommended_products",
    "_handle_catalog_products",
    "_catalog_products_text",
]
