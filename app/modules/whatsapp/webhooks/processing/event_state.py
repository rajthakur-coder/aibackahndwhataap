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
from app.models.whatsapp import Message, WebhookEvent
from app.modules.crm.agent.agent_service import bot_setting_enabled, get_bot_settings, process_agent_message
from app.modules.ai.tools.ai_tools_service import ToolDecision, decide_tool_for_message, run_ai_tool
from app.modules.crm.memory.conversation_memory_service import remember_last_products
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.live_chat.live_chat_service import serialize_message
from app.modules.whatsapp.live_chat.socket import publish_live_chat_event
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

def _start_processing_event(db: Session, event: WebhookEvent, timing: WebhookTiming) -> int:
    attempt_number = (event.attempts or 0) + 1
    with timing.stage("webhook_received"):
        event.attempts = attempt_number
        event.status = "processing"
        event.error = None
        db.commit()
    return attempt_number

async def _persist_incoming_message(
    db: Session,
    event: WebhookEvent,
    phone: str,
    text: str,
    timing: WebhookTiming,
) -> None:
    with timing.stage("message_persist"):
        with timing.stage("message_save_incoming"):
            display_text = _incoming_display_text(event, text)
            incoming_payload = _incoming_message_payload(db, phone, event, text, display_text)
            message_type = _incoming_message_type(event)
            incoming_row = save_message(
                db,
                phone,
                display_text,
                "incoming",
                whatsapp_message_id=event.external_id,
                message_type=message_type,
                payload=incoming_payload,
                tenant_id=event.tenant_id,
            )
        with timing.stage("live_chat_broadcast"):
            await publish_live_chat_event(
                {
                    "type": "live_chat_message",
                    "direction": "in",
                    "contact": phone,
                    "message": serialize_message(incoming_row),
                },
                tenant_id=event.tenant_id,
            )
        with timing.stage("analytics_enqueue"):
            start_log_interactive_click(phone, event.external_id, event.payload, tenant_id=event.tenant_id)
        with timing.stage("memory_enqueue"):
            start_remember_last_question(phone, text, tenant_id=event.tenant_id)


def _incoming_display_text(event: WebhookEvent, fallback: str) -> str:
    try:
        payload = json.loads(event.payload or "{}")
    except json.JSONDecodeError:
        payload = {}

    message_payload = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message_payload, dict):
        message_payload = payload if isinstance(payload, dict) else {}
    interactive = message_payload.get("interactive")
    if not isinstance(interactive, dict):
        return fallback

    reply = None
    if interactive.get("type") == "list_reply":
        reply = interactive.get("list_reply")
    elif interactive.get("type") == "button_reply":
        reply = interactive.get("button_reply")

    if isinstance(reply, dict):
        if str(fallback or "").startswith(("catalog category ", "catalog dynamic category ", "catalog page ")):
            return fallback
        title = str(reply.get("title") or "").strip()
        if title:
            return title

    return fallback


def _incoming_message_payload(
    db: Session,
    phone: str,
    event: WebhookEvent,
    raw_text: str,
    display_text: str,
) -> dict | None:
    payload: dict = {}
    if display_text != raw_text:
        payload["raw_text"] = raw_text

    message_payload = _event_message_payload(event)
    media_payload = _incoming_media_payload(message_payload)
    if media_payload:
        payload.update(media_payload)

    reply_context = _latest_interactive_reply_context(db, phone, event.tenant_id, message_payload)
    if reply_context:
        payload["reply_context"] = reply_context

    return payload or None


def _incoming_message_type(event: WebhookEvent) -> str:
    message_payload = _event_message_payload(event)
    return str(message_payload.get("type") or "text")


def _event_message_payload(event: WebhookEvent) -> dict:
    try:
        payload = json.loads(event.payload or "{}")
    except json.JSONDecodeError:
        payload = {}

    message_payload = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message_payload, dict):
        return message_payload
    return payload if isinstance(payload, dict) else {}


def _incoming_media_payload(message_payload: dict) -> dict | None:
    message_type = str(message_payload.get("type") or "")
    if message_type not in {"image", "video", "audio", "document", "sticker"}:
        return None

    media = message_payload.get(message_type)
    if not isinstance(media, dict):
        return None

    media_id = str(media.get("id") or "").strip()
    media_url = f"/whatsapp-message/media/{media_id}" if media_id else None
    payload: dict = {
        "media": media,
        "media_id": media_id or None,
        "mime_type": media.get("mime_type"),
        "caption": media.get("caption"),
        "filename": media.get("filename"),
        "voice": media.get("voice"),
    }
    if media_url:
        payload["media_url"] = media_url
        payload[f"{message_type}_url"] = media_url

    return {key: value for key, value in payload.items() if value not in (None, "")}


def _latest_interactive_reply_context(db: Session, phone: str, tenant_id: str | None = None, message_payload: dict | None = None) -> dict | None:
    tenant_id = normalize_tenant_id(tenant_id or DEFAULT_TENANT_ID)
    quoted_message_id = _quoted_message_id(message_payload)

    row = None
    if quoted_message_id:
        row = db.execute(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.phone == phone,
                Message.direction == "outgoing",
                Message.whatsapp_message_id == quoted_message_id,
                Message.payload.is_not(None),
            )
            .limit(1)
        ).scalars().first()

    if quoted_message_id and row is None:
        row = db.execute(
            select(Message)
            .where(
                Message.direction == "outgoing",
                Message.whatsapp_message_id == quoted_message_id,
                Message.payload.is_not(None),
            )
            .limit(1)
        ).scalars().first()

    if row is None:
        row = db.execute(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.phone == phone,
                Message.direction == "outgoing",
                Message.message_type.in_(["buttons", "list"]),
                Message.payload.is_not(None),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).scalars().first()

    return _interactive_reply_context_from_message(row)



def _quoted_message_id(message_payload: dict) -> str | None:
    context = message_payload.get("context") if isinstance(message_payload, dict) else None
    if not isinstance(context, dict):
        return None
    quoted_id = str(context.get("id") or "").strip()
    return quoted_id or None


def _interactive_reply_context_from_message(row: Message | None) -> dict | None:
    if not row or not row.payload:
        return None

    try:
        payload = json.loads(row.payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title and not body:
        return None
    return {
        "title": title,
        "body": body,
    }


def _mark_processed(db: Session, event: WebhookEvent, timing: WebhookTiming | None = None) -> None:
    if timing:
        with timing.stage("mark_processed"):
            event.status = "processed"
            event.processed_at = datetime.utcnow()
            db.commit()
    else:
        event.status = "processed"
        event.processed_at = datetime.utcnow()
        db.commit()
    if timing:
        timing.log("processed")

__all__ = [
    "_start_processing_event",
    "_persist_incoming_message",
    "_incoming_display_text",
    "_mark_processed",
]
