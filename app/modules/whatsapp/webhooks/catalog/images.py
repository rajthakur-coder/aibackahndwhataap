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

from app.modules.whatsapp.webhooks.catalog.products import *

async def _handle_product_image(context: WebhookProcessingContext) -> bool:
    with context.timing.stage("shopify_product_image_fetch"):
        product_image = await find_cached_product_image(
            context.db,
            context.query_text,
            entities=context.understanding.entities,
            phone=context.phone,
        )
    if not product_image:
        return False

    remember_last_products(context.db, context.phone, [product_image])
    with context.timing.stage("whatsapp_send"):
        cta_sent = await _try_send_product_cta(context.phone, product_image, "Buy now")
    if cta_sent:
        save_message(
            context.db,
            context.phone,
            f"[cta_url] {product_image['title']}",
            "outgoing",
            message_type="cta_url",
            payload={
                "title": product_image.get("title") or "Product",
                "caption": product_image.get("caption") or "",
                "image_url": product_image.get("image_url"),
                "product_url": product_image.get("product_url"),
                "button_text": "Buy now",
            },
        )
        await _queue_cross_sell_products(context.db, context.phone, context.query_text, [product_image])
        return True

    body_text = _localized(
        context.reply_language,
        "You can view this product detail.",
        "Product detail dekh sakte hain.",
    )
    if await _send_product_list_reply(
        context,
        [product_image],
        "Product",
        body_text,
        f"[product_list] {product_image['title']}",
    ):
        await _queue_cross_sell_products(context.db, context.phone, context.query_text, [product_image])
        return True

    await _send_product_image_or_fallback(context, product_image)
    await _queue_cross_sell_products(context.db, context.phone, context.query_text, [product_image])
    return True

async def _send_product_image_or_fallback(context: WebhookProcessingContext, product_image: dict) -> None:
    try:
        with context.timing.stage("whatsapp_send"):
            await run_in_threadpool(
                send_whatsapp_image,
                context.phone,
                product_image["image_url"],
                product_image["caption"],
            )
        save_message(
            context.db,
            context.phone,
            f"[image] {product_image['caption']}",
            "outgoing",
            message_type="image",
            payload={
                "caption": product_image.get("caption") or "",
                "image_url": product_image.get("image_url"),
                "title": product_image.get("title") or "Product",
                "product_url": product_image.get("product_url"),
            },
        )
    except Exception as exc:
        context.db.add(
            AgentAction(
                phone=context.phone,
                action_type="product_image_send_failed",
                status="failed",
                payload=json.dumps(
                    {
                        "title": product_image["title"],
                        "image_url": product_image["image_url"],
                    }
                ),
                result=json.dumps({"error": str(exc)}),
            )
        )
        context.db.commit()
        fallback_text = (
            _localized(
                context.reply_language,
                "I could not send the image, but here are the product details:\n",
                "Image send nahi ho payi, lekin product detail yeh hai:\n",
            )
            + f"{product_image['caption']}"
        )
        await _send_text_reply(context, fallback_text)

__all__ = [
    "_handle_product_image",
    "_send_product_image_or_fallback",
]
