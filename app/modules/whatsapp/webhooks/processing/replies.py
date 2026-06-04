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
from app.modules.ai.orchestrator import orchestrate_message
from app.modules.automation.browse_event_service import create_browse_no_buy_event
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
    send_cross_sell_products as _send_cross_sell_products,
    understanding_context as _understanding_context,
)
from app.shared.arq_queue import enqueue_whatsapp_product_images
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
from app.modules.whatsapp.client.interactive_client_service import send_whatsapp_reply_buttons
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.whatsapp.webhooks.processing.pipeline import WebhookProcessingContext
from app.modules.whatsapp.webhooks.flows.commerce_flows import send_bundle_push


async def _send_ai_reply(context: WebhookProcessingContext) -> None:
    ai_reply = context.agent_state["reply_override"]
    if not ai_reply:
        try:
            with context.timing.stage("orchestrator"):
                orchestrator_response = orchestrate_message(
                    context.db,
                    phone=context.phone,
                    message=context.text,
                    tenant_id=context.tenant_id,
                    understanding=context.understanding,
                )
                ai_reply = orchestrator_response.reply
                _log_orchestrator_result(context, orchestrator_response)
        except Exception as exc:
            context.db.rollback()
            try:
                context.db.add(
                    AgentAction(
                        tenant_id=context.tenant_id,
                        phone=context.phone,
                        action_type="ai_reply_failed_fallback_used",
                        status="failed",
                        payload=json.dumps({"message": context.text}),
                        result=json.dumps({"error": str(exc)}),
                    )
                )
                context.db.commit()
            except Exception:
                context.db.rollback()
            ai_reply = context.bot_settings.fallback_message or (
                "I do not have that information right now. I can connect you with our support team."
            )
    await _send_text_reply(context, ai_reply)
    await _send_post_tool_interactive(context, locals().get("orchestrator_response"))

async def _send_post_tool_interactive(context: WebhookProcessingContext, orchestrator_response) -> None:
    if not orchestrator_response:
        return
    tool_name = orchestrator_response.selected_tool
    data = orchestrator_response.tool_result.data if hasattr(orchestrator_response.tool_result, "data") else {}
    if orchestrator_response.tool_result.status == "needs_confirmation" and isinstance(data, dict):
        buttons = data.get("buttons") or []
        if buttons:
            body = data.get("summary") or orchestrator_response.tool_result.message or "Please confirm before I continue."
            try:
                await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Confirm")
                save_message(context.db, context.phone, "[buttons] Confirm action", "outgoing", message_type="buttons", payload={"title": "Confirm", "body": body, "buttons": buttons})
            except Exception:
                pass
        return
    if tool_name == "get_bundle_recommendations" and isinstance(data, dict):
        recommendations = data.get("recommendations") or []
        await send_bundle_push(context, recommendations)

def _log_orchestrator_result(context: WebhookProcessingContext, orchestrator_response) -> None:
    context.db.add(
        AgentAction(
            phone=context.phone,
            action_type="whatsapp_orchestrator_reply",
            status="logged",
            payload=json.dumps(
                {
                    "message": context.text,
                    "normalized_query": context.query_text,
                    "intent": orchestrator_response.intent,
                    "selected_tool": orchestrator_response.selected_tool,
                    "confidence": orchestrator_response.confidence,
                },
                ensure_ascii=True,
            ),
            result=json.dumps(
                {
                    "tool": orchestrator_response.tool_result.tool_name,
                    "tool_status": orchestrator_response.tool_result.status,
                    "reply_length": len(orchestrator_response.reply or ""),
                },
                ensure_ascii=True,
            ),
        )
    )
    context.db.commit()

def _run_ai_tool_for_context(context: WebhookProcessingContext) -> dict:
    if context.understanding.confidence >= 0.45 and context.understanding.tool:
        tool_decision = ToolDecision(
            context.understanding.tool,
            f"query_understanding:{context.understanding.intent}",
        )
    else:
        tool_decision = decide_tool_for_message(context.query_text)
    with context.timing.stage("tool"):
        tool_result = run_ai_tool(context.db, context.phone, context.query_text, tool_decision)
    context.db.add(
        AgentAction(
            phone=context.phone,
            action_type="ai_tool_selected",
            status="logged",
            payload=json.dumps(
                {
                    "message": context.text,
                    "normalized_query": context.query_text,
                    "tool": tool_result["tool"],
                    "reason": tool_result["reason"],
                }
            ),
            result=json.dumps({"data_count": len(tool_result.get("data") or [])}),
        )
    )
    context.db.commit()
    return tool_result

async def _send_product_carousel_reply(
    context: WebhookProcessingContext,
    products: list[dict],
    body_text: str,
    saved_message: str,
) -> bool:
    with context.timing.stage("whatsapp_send"):
        carousel_sent = await _try_send_product_carousel(context.phone, products, body_text)
    if carousel_sent:
        save_message(
            context.db,
            context.phone,
            saved_message,
            "outgoing",
            message_type="carousel",
            payload={"body": body_text, "products": _product_cards_payload(products)},
        )
        _queue_browse_no_buy(context, products)
    return carousel_sent

async def _send_product_list_reply(
    context: WebhookProcessingContext,
    products: list[dict],
    header_text: str,
    body_text: str,
    saved_message: str,
) -> bool:
    with context.timing.stage("whatsapp_send"):
        product_list_sent = await _try_send_product_list(context.phone, products, header_text, body_text)
    if product_list_sent:
        save_message(
            context.db,
            context.phone,
            saved_message,
            "outgoing",
            message_type="product_list",
            payload={
                "header": header_text,
                "body": body_text,
                "products": _product_cards_payload(products),
            },
        )
        _queue_browse_no_buy(context, products)
    return product_list_sent


def _queue_browse_no_buy(context: WebhookProcessingContext, products: list[dict]) -> None:
    try:
        create_browse_no_buy_event(
            context.db,
            phone=context.phone,
            tenant_id=context.tenant_id,
            products=products[:5],
        )
    except Exception:
        pass

async def _send_text_reply(context: WebhookProcessingContext, text: str) -> None:
    with context.timing.stage("whatsapp_send"):
        await run_in_threadpool(send_whatsapp_message, context.phone, text)
    save_message(context.db, context.phone, text, "outgoing")


def _product_cards_payload(products: list[dict]) -> list[dict]:
    cards = []
    for product in products[:10]:
        cards.append(
            {
                "title": product.get("title") or "Product",
                "price": product.get("price_min") or product.get("price") or product.get("price_max") or "",
                "image_url": product.get("image_url"),
                "product_url": product.get("product_url"),
                "caption": product.get("caption") or product.get("description") or "",
            }
        )
    return cards

async def _queue_cross_sell_products(
    db: Session,
    phone: str,
    text: str,
    base_products: list[dict],
) -> None:
    if not base_products:
        return
    try:
        await _send_cross_sell_products(db, phone, text, base_products)
    except Exception as exc:
        db.add(
            AgentAction(
                phone=phone,
                action_type="cross_sell_send_failed",
                status="failed",
                payload=json.dumps({"base_product_count": len(base_products)}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()

async def _queue_product_images(
    db: Session,
    phone: str,
    products: list[dict],
    caption_mode: str,
    failure_action: str,
) -> None:
    image_products = [product for product in products[:2] if product.get("image_url")]
    if not image_products:
        return
    try:
        await enqueue_whatsapp_product_images(phone, image_products, caption_mode, failure_action)
    except Exception as exc:
        db.add(
            AgentAction(
                phone=phone,
                action_type="product_images_enqueue_failed",
                status="failed",
                payload=json.dumps({"image_count": len(image_products), "failure_action": failure_action}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()

__all__ = [
    "_send_ai_reply",
    "_run_ai_tool_for_context",
    "_send_product_carousel_reply",
    "_send_product_list_reply",
    "_send_text_reply",
    "_queue_cross_sell_products",
    "_queue_product_images",
]
