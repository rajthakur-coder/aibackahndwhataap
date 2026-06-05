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
from app.modules.ecommerce.catalog.shopify_cache_service import is_catalog_request as _is_shopify_catalog_request
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

PRODUCT_DETAIL_RE = re.compile(
    r"\b(?:dimension|dimensions|measurement|measurements|size|sizes|capacity|volume)\b",
    re.I,
)
PRODUCT_DETAIL_STRONG_RE = re.compile(r"\b(?:dimension|dimensions|measurement|measurements|capacity|volume)\b", re.I)
PRODUCT_DETAIL_QUESTION_RE = re.compile(
    r"\b(?:what|which|kya|kitna|kitni|batao|tell|available|options?|is|are|hai|hain)\b",
    re.I,
)
MEASUREMENT_VALUE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:cm|mm|m|inch|inches|in|ft|feet|ml|l|ltr|litre|liter|kg|g)\b",
    re.I,
)


async def _handle_product_detail_question(context: WebhookProcessingContext) -> bool:
    if not _is_product_detail_question(context.query_text):
        return False

    with context.timing.stage("shopify_product_detail_fetch"):
        catalog_products = await find_cached_catalog_products(
            context.db,
            context.query_text,
            limit=3,
            entities=context.understanding.entities,
            phone=context.phone,
        )
    catalog_products = catalog_products or []
    if not catalog_products:
        fallback_query = _fallback_product_query(context.query_text)
        if fallback_query and fallback_query != context.query_text:
            with context.timing.stage("shopify_product_detail_fallback_fetch"):
                catalog_products = await find_cached_catalog_products(
                    context.db,
                    fallback_query,
                    limit=3,
                    entities=context.understanding.entities,
                    phone=context.phone,
                )
            catalog_products = catalog_products or []

    if not catalog_products:
        await _send_text_reply(
            context,
            _localized(
                context.reply_language,
                "I could not find that product in the enabled catalog collections. Please try the product name again.",
                "Enabled catalog collections me ye product nahi mila. Kripya product name dobara bhejein.",
            ),
        )
        return True

    product = catalog_products[0]
    await _send_text_reply(context, _product_detail_answer(context, product))
    remember_last_products(context.db, context.phone, catalog_products)
    return True


async def _handle_top_selling_products(context: WebhookProcessingContext) -> bool:
    if not is_top_selling_request(context.query_text) and context.understanding.intent != "top_selling_products":
        return False
    top_selling_limit = max(context.requested_limit, 10)
    with context.timing.stage("shopify_top_selling_fetch"):
        top_selling_products = await find_cached_top_selling_products(
            context.db,
            limit=top_selling_limit,
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
    if await _send_product_list_reply(
        context,
        top_selling_products,
        "Top selling products",
        body_text,
        "[product_list] Top selling products",
    ):
        return True
    if await _send_product_carousel_reply(context, top_selling_products, body_text, "[carousel] Top selling products"):
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
    product_limit = max(context.requested_limit, 10)
    with context.timing.stage("shopify_recommendations_fetch"):
        recommended_products = await find_cached_product_recommendations(
            context.db,
            context.query_text,
            limit=product_limit,
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
    if not _is_product_search_request(context):
        return False
    product_limit = max(context.requested_limit, 10)
    with context.timing.stage("shopify_catalog_fetch"):
        catalog_products = await find_cached_catalog_products(
            context.db,
            context.query_text,
            limit=product_limit,
            entities=context.understanding.entities,
            phone=context.phone,
    )
    catalog_products = catalog_products or []
    if not catalog_products:
        fallback_query = _fallback_product_query(context.query_text)
        if fallback_query and fallback_query != context.query_text:
            with context.timing.stage("shopify_catalog_fallback_fetch"):
                catalog_products = await find_cached_catalog_products(
                    context.db,
                    fallback_query,
                    limit=product_limit,
                    entities=context.understanding.entities,
                    phone=context.phone,
                )
            catalog_products = catalog_products or []
    if not catalog_products:
        await _send_text_reply(
            context,
            _localized(
                context.reply_language,
                "I could not find an exact match in the enabled catalog collections. Try another size, material, or category.",
                "Enabled catalog collections me exact match nahi mila. Aap koi aur size, material, ya category try kar sakte hain.",
            ),
        )
        return True

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

def _is_product_search_request(context: WebhookProcessingContext) -> bool:
    if _is_shopify_catalog_request(context.query_text):
        return True
    if context.understanding.intent in {"catalog_request", "image_request", "price_question", "top_selling_products"}:
        return True
    if context.understanding.tool == "search_products":
        return True
    return False

def _is_product_detail_question(query: str) -> bool:
    text = query or ""
    if not PRODUCT_DETAIL_RE.search(text):
        return False
    if not PRODUCT_DETAIL_STRONG_RE.search(text) and not PRODUCT_DETAIL_QUESTION_RE.search(text):
        return False
    return _is_shopify_catalog_request(text) or bool(_fallback_product_query(text))

def _fallback_product_query(query: str) -> str:
    text = re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:cm|mm|m|inch|inches|in|ft|feet|ml|l|ltr|litre|liter|kg|g)\b",
        " ",
        query or "",
        flags=re.I,
    )
    text = re.sub(r"\b(?:what|about|dimension|dimensions|size|of|is|the|for|need|want)\b", " ", text, flags=re.I)
    text = " ".join(text.split())
    return text

def _product_detail_answer(context: WebhookProcessingContext, product: dict) -> str:
    title = str(product.get("title") or "this product").strip()
    detail_lines = _product_detail_lines(product)
    if detail_lines:
        lines = [f"{title} details:"]
        lines.extend(detail_lines)
        if product.get("product_url"):
            lines.append(f"Product link: {tracking_url(product['product_url'], phone=context.phone, source='product_detail', title=title)}")
        return "\n".join(lines)

    price = product.get("price") or product.get("price_min") or ""
    parts = [f"I found {title}, but dimension/capacity details are not listed in the Shopify product data."]
    if price:
        parts.append(f"Price starts at {price}.")
    if product.get("product_url"):
        parts.append(f"Product link: {tracking_url(product['product_url'], phone=context.phone, source='product_detail', title=title)}")
    return " ".join(parts)

def _product_detail_lines(product: dict) -> list[str]:
    lines = []
    option_lines = _option_detail_lines(product)
    if option_lines:
        lines.extend(option_lines)

    description_values = _measurement_values(str(product.get("description") or ""))
    if description_values and not _values_already_covered(description_values, lines):
        lines.append("Measurements mentioned: " + ", ".join(description_values[:8]))

    if not lines:
        variant_values = _variant_detail_values(product)
        if variant_values:
            lines.append("Available variants: " + ", ".join(variant_values[:10]))
    return lines

def _option_detail_lines(product: dict) -> list[str]:
    options = product.get("options") or []
    lines = []
    for index, option in enumerate(options[:3], start=1):
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or f"Option {index}").strip()
        raw_values = option.get("values") or []
        values = [str(value).strip() for value in raw_values if str(value or "").strip()]
        if not values:
            continue
        relevant = _is_detail_option(name, values)
        if not relevant:
            continue
        available_values = _available_option_values(product, index)
        if available_values:
            values = [value for value in values if value.lower() in available_values] or values
        lines.append(f"{name}: {', '.join(_dedupe(values)[:10])}")
    return lines

def _is_detail_option(name: str, values: list[str]) -> bool:
    lowered_name = name.lower()
    if any(term in lowered_name for term in ("size", "capacity", "volume", "dimension", "measurement")):
        return True
    return any(MEASUREMENT_VALUE_RE.search(value) for value in values)

def _available_option_values(product: dict, position: int) -> set[str]:
    values = set()
    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        quantity = variant.get("inventory_quantity")
        if isinstance(quantity, int) and quantity <= 0:
            continue
        value = str(variant.get(f"option{position}") or "").strip()
        if value:
            values.add(value.lower())
    return values

def _variant_detail_values(product: dict) -> list[str]:
    values = []
    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        quantity = variant.get("inventory_quantity")
        if isinstance(quantity, int) and quantity <= 0:
            continue
        for key in ("title", "option1", "option2", "option3"):
            value = str(variant.get(key) or "").strip()
            if value and value.lower() != "default title" and MEASUREMENT_VALUE_RE.search(value):
                values.append(value)
    return _dedupe(values)

def _measurement_values(text: str) -> list[str]:
    return _dedupe(match.group(0).upper().replace(" ", "") for match in MEASUREMENT_VALUE_RE.finditer(text or ""))

def _values_already_covered(values: list[str], lines: list[str]) -> bool:
    haystack = " ".join(lines).lower().replace(" ", "")
    return all(value.lower().replace(" ", "") in haystack for value in values)

def _dedupe(values) -> list[str]:
    seen = set()
    result = []
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result

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
    "_handle_product_detail_question",
    "_handle_top_selling_products",
    "_handle_recommended_products",
    "_handle_catalog_products",
    "_catalog_products_text",
]
