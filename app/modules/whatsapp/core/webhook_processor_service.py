import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction, HandoffTicket
from app.models.ecommerce import EcommerceConnection
from app.models.whatsapp import WebhookEvent
from app.modules.crm.core.crm_agent_service import bot_setting_enabled, get_bot_settings, process_agent_message
from app.modules.ai.core.ai_tools_service import ToolDecision, decide_tool_for_message, run_ai_tool
from app.modules.crm.core.conversation_memory_service import remember_last_products, remember_last_question
from app.modules.whatsapp.core.messages_service import save_message
from app.modules.whatsapp.core.live_chat_service import serialize_message
from app.modules.whatsapp.core.live_chat_socket import live_chat_manager
from app.modules.whatsapp.core.webhook_observability_service import WebhookTiming
from app.modules.whatsapp.core.webhook_response_service import (
    CATALOG_CATEGORY_LABELS,
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
from app.modules.ai.core.openai_chat_service import generate_ai_reply
from app.modules.ai.core.query_understanding_service import understand_message
from app.modules.ai.core.sales_recommendations_service import (
    is_top_selling_request,
    recommendation_intro,
)
from app.modules.ecommerce.core.shopify_cache_service import (
    find_cached_shopify_catalog_products,
    find_cached_shopify_order_status,
    find_cached_shopify_product_image,
    find_cached_shopify_product_recommendations,
    find_cached_shopify_top_selling_products,
)
from app.modules.whatsapp.core.whatsapp_client_service import (
    mark_whatsapp_message_read_with_typing,
    send_whatsapp_image,
    send_whatsapp_message,
)


def parse_whatsapp_messages(payload: dict) -> list[dict]:
    parsed_messages = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_id = message.get("id")
                text = message.get("text", {}).get("body")
                phone = message.get("from")
                interactive = message.get("interactive") or {}
                list_reply = interactive.get("list_reply") if interactive.get("type") == "list_reply" else None
                button_reply = interactive.get("button_reply") if interactive.get("type") == "button_reply" else None
                if list_reply:
                    reply_id = str(list_reply.get("id") or "")
                    title = str(list_reply.get("title") or "")
                    if reply_id.startswith("catalog:page:"):
                        text = f"catalog page {reply_id.removeprefix('catalog:page:')}".strip()
                    elif reply_id.startswith("catalog:category:"):
                        text = f"catalog dynamic category {reply_id.removeprefix('catalog:category:')} {title}".strip()
                    elif reply_id.startswith("catalog:"):
                        text = f"catalog category {reply_id.removeprefix('catalog:')} {title}".strip()
                    else:
                        text = title or reply_id
                elif button_reply:
                    reply_id = str(button_reply.get("id") or "")
                    title = str(button_reply.get("title") or "")
                    if reply_id.startswith("catalog:more:"):
                        parts = reply_id.split(":")
                        category = parts[2] if len(parts) > 2 else ""
                        page = parts[3] if len(parts) > 3 else "1"
                        if category in CATALOG_CATEGORY_LABELS:
                            text = f"catalog category {category} page {page}".strip()
                        else:
                            text = f"catalog dynamic category {category} page {page}".strip()
                    elif reply_id == "menu:catalog":
                        text = "catalog dikhao"
                    elif reply_id == "menu:order_status":
                        text = "track order"
                    elif reply_id == "menu:human":
                        text = "talk to human"
                    else:
                        text = title or reply_id

                if phone and text:
                    parsed_messages.append(
                        {
                            "id": message_id,
                            "phone": phone,
                            "text": text,
                            "payload": message,
                        }
                    )

    return parsed_messages


def get_or_create_webhook_event(db: Session, incoming: dict) -> tuple[WebhookEvent, bool]:
    external_id = incoming.get("id")
    event = None
    if external_id:
        event = db.execute(
            select(WebhookEvent).where(WebhookEvent.external_id == external_id)
        ).scalars().first()
    if event:
        return event, False

    event = WebhookEvent(
        external_id=external_id,
        phone=incoming["phone"],
        message_text=incoming["text"],
        payload=json.dumps(incoming.get("payload") or {}),
        status="pending",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True


def should_process_webhook_event(event: WebhookEvent, created: bool) -> bool:
    if created:
        return True
    return event.status in {"pending", "failed"}


def mark_webhook_event_failed(db: Session, event: WebhookEvent, exc: Exception) -> None:
    event.status = "failed"
    event.error = str(exc)
    db.commit()


async def process_webhook_event(event: WebhookEvent, db: Session) -> None:
    phone = event.phone or ""
    text = event.message_text or ""
    timing = WebhookTiming(db, phone, event.id)
    attempt_number = (event.attempts or 0) + 1
    with timing.stage("webhook_received"):
        event.attempts = attempt_number
        event.status = "processing"
        event.error = None
        db.commit()

    if attempt_number == 1:
        with timing.stage("message_persist"):
            incoming_row = save_message(
                db,
                phone,
                text,
                "incoming",
                whatsapp_message_id=event.external_id,
            )
            await live_chat_manager.broadcast(
                {
                    "type": "live_chat_message",
                    "direction": "in",
                    "contact": phone,
                    "message": serialize_message(incoming_row),
                }
            )
            remember_last_question(db, phone, text)

    with timing.stage("bot_settings"):
        bot_settings = get_bot_settings(db)
    if not bot_setting_enabled(bot_settings.bot_enabled):
        db.add(
            AgentAction(
                phone=phone,
                action_type="bot_disabled_auto_reply_skipped",
                status="skipped",
                payload=json.dumps({"message": text}),
            )
        )
        db.commit()
        _mark_processed(db, event, timing)
        return
    if not _active_store_bot_enabled(db, phone):
        db.add(
            AgentAction(
                phone=phone,
                action_type="store_bot_disabled_auto_reply_skipped",
                status="skipped",
                payload=json.dumps({"message": text}),
            )
        )
        db.commit()
        _mark_processed(db, event, timing)
        return

    with timing.stage("whatsapp_typing"):
        await _try_mark_read_with_typing(db, event)

    with timing.stage("handoff_check"):
        active_handoff = _active_handoff_ticket(db, phone)
    if active_handoff:
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
        _mark_processed(db, event, timing)
        return

    with timing.stage("intent"):
        understanding = understand_message(text)
    query_text = understanding.normalized_query or text
    db.add(
        AgentAction(
            phone=phone,
            action_type="query_understanding",
            status="logged",
            payload=json.dumps(
                {
                    "message": text,
                    "normalized_query": query_text,
                    "intent": understanding.intent,
                    "entities": understanding.entities,
                    "confidence": understanding.confidence,
                    "tool": understanding.tool,
                    "source": understanding.source,
                }
            ),
        )
    )
    db.commit()

    with timing.stage("crm_agent"):
        agent_state = process_agent_message(db, phone, query_text)
    if agent_state["intent"] == "human_handoff" and not _within_business_hours(bot_settings):
        offline_text = bot_settings.offline_message or (
            "Our support team is offline right now. Your request is noted and the team will reply during business hours."
        )
        with timing.stage("whatsapp_send"):
            await run_in_threadpool(send_whatsapp_message, phone, offline_text)
        save_message(db, phone, offline_text, "outgoing")
        _mark_processed(db, event, timing)
        return
    if agent_state["intent"] == "order_status":
        with timing.stage("shopify"):
            shopify_order_reply = await find_cached_shopify_order_status(db, phone, query_text)
        if shopify_order_reply:
            agent_state["reply_override"] = shopify_order_reply
    requested_limit = _requested_limit_from_understanding(understanding, query_text)
    reply_language = _reply_language(query_text, bot_settings)
    if understanding.intent in {"greeting", "menu_request"} or _is_main_menu_request(query_text):
        with timing.stage("whatsapp_send"):
            menu_sent = await _try_send_main_menu(phone, reply_language, bot_settings)
        if menu_sent:
            save_message(db, phone, "[buttons] Main menu", "outgoing")
            _mark_processed(db, event, timing)
            return

    if _is_catalog_page_request(query_text):
        with timing.stage("shopify"):
            category_list_sent = await _try_send_catalog_category_list(
                db,
                phone,
                reply_language,
                page=_catalog_page_number(query_text),
            )
        if category_list_sent:
            save_message(db, phone, "[list] Catalog categories", "outgoing")
            _mark_processed(db, event, timing)
            return

    selected_catalog_category = _selected_catalog_category(query_text)
    if selected_catalog_category:
        category_page = _selected_catalog_product_page(query_text)
        category_fetch_limit = requested_limit + 1
        category_offset = (category_page - 1) * requested_limit
        with timing.stage("shopify"):
            category_products_page = await _products_for_catalog_category(
                db,
                phone,
                selected_catalog_category,
                category_fetch_limit,
                offset=category_offset,
            )
        has_more_category_products = (
            selected_catalog_category != "best_sellers"
            and len(category_products_page) > requested_limit
        )
        category_products = category_products_page[:requested_limit]
        if category_products:
            remember_last_products(db, phone, category_products)
            label_key = selected_catalog_category.removeprefix("dynamic:")
            label = CATALOG_CATEGORY_LABELS.get(
                selected_catalog_category,
                CATALOG_CATEGORY_LABELS.get(label_key, label_key.replace("_", " ").title()),
            )
            with timing.stage("whatsapp_send"):
                carousel_sent = await _try_send_product_carousel(
                    phone,
                    category_products,
                    _localized(reply_language, f"You can browse {label} products.", f"{label} products dekh sakte hain."),
                )
            if carousel_sent:
                save_message(db, phone, f"[carousel] {label}", "outgoing")
                if has_more_category_products:
                    with timing.stage("whatsapp_send"):
                        await _try_send_category_more_button(
                            phone,
                            selected_catalog_category,
                            category_page + 1,
                            label,
                            reply_language,
                        )
                _mark_processed(db, event, timing)
                return
            with timing.stage("whatsapp_send"):
                product_list_sent = await _try_send_product_list(
                    phone,
                    category_products,
                    label,
                    _localized(reply_language, f"You can browse {label} products.", f"{label} products dekh sakte hain."),
                )
            if product_list_sent:
                save_message(db, phone, f"[product_list] {label}", "outgoing")
                if has_more_category_products:
                    with timing.stage("whatsapp_send"):
                        await _try_send_category_more_button(
                            phone,
                            selected_catalog_category,
                            category_page + 1,
                            label,
                            reply_language,
                        )
                _mark_processed(db, event, timing)
                return
        fallback_text = _localized(
            reply_language,
            "No products found in this category right now. You can try All products.",
            "Is category me abhi products nahi mile. Aap All products try kar sakte hain.",
        )
        with timing.stage("whatsapp_send"):
            await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
        save_message(db, phone, fallback_text, "outgoing")
        _mark_processed(db, event, timing)
        return

    if _looks_like_catalog_request(query_text):
        with timing.stage("shopify"):
            category_list_sent = await _try_send_catalog_category_list(
                db,
                phone,
                reply_language,
                page=_catalog_page_number(query_text),
            )
        if category_list_sent:
            save_message(db, phone, "[list] Catalog categories", "outgoing")
            _mark_processed(db, event, timing)
            return

    if is_top_selling_request(query_text) or understanding.intent == "top_selling_products":
        with timing.stage("shopify"):
            top_selling_products = await find_cached_shopify_top_selling_products(db, limit=requested_limit, phone=phone)
        if top_selling_products:
            remember_last_products(db, phone, top_selling_products)
            with timing.stage("whatsapp_send"):
                carousel_sent = await _try_send_product_carousel(
                    phone,
                    top_selling_products,
                    _localized(reply_language, "These are the top-selling products.", "Ye top-selling products hain."),
                )
            if carousel_sent:
                save_message(db, phone, "[carousel] Top selling products", "outgoing")
                _mark_processed(db, event, timing)
                return
            with timing.stage("whatsapp_send"):
                product_list_sent = await _try_send_product_list(
                    phone,
                    top_selling_products,
                    "Top selling products",
                    _localized(reply_language, "These are the top-selling products.", "Ye top-selling products hain."),
                )
            if product_list_sent:
                save_message(db, phone, "[product_list] Top selling products", "outgoing")
            else:
                recommendation_text = recommendation_intro(text, top_selling_products)
                with timing.stage("whatsapp_send"):
                    await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
                save_message(db, phone, recommendation_text, "outgoing")

                await _queue_product_images(
                    db,
                    phone,
                    top_selling_products,
                    caption_mode="recommendation",
                    failure_action="top_selling_image_send_failed",
                )
        else:
            recommendation_text = _localized(
                reply_language,
                "Sales data is not available yet to calculate top-selling products.",
                "Abhi top-selling products nikalne ke liye order/sales data available nahi hai.",
            )
            with timing.stage("whatsapp_send"):
                await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
            save_message(db, phone, recommendation_text, "outgoing")

        _mark_processed(db, event, timing)
        return

    with timing.stage("shopify"):
        recommended_products = await find_cached_shopify_product_recommendations(
            db,
            query_text,
            limit=requested_limit,
            entities=understanding.entities,
            phone=phone,
        )
    if recommended_products:
        remember_last_products(db, phone, recommended_products)
        with timing.stage("whatsapp_send"):
            carousel_sent = await _try_send_product_carousel(
                phone,
                recommended_products,
                _localized(reply_language, "Matching products for you.", "Aapke liye matching products."),
            )
        if carousel_sent:
            save_message(db, phone, "[carousel] Recommended products", "outgoing")
            await _queue_cross_sell_products(db, phone, query_text, recommended_products)
            _mark_processed(db, event, timing)
            return
        with timing.stage("whatsapp_send"):
            product_list_sent = await _try_send_product_list(
                phone,
                recommended_products,
                "Recommended products",
                _localized(reply_language, "Matching products for you.", "Aapke liye matching products."),
            )
        if product_list_sent:
            save_message(db, phone, "[product_list] Recommended products", "outgoing")
        else:
            recommendation_text = recommendation_intro(text, recommended_products)
            with timing.stage("whatsapp_send"):
                await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
            save_message(db, phone, recommendation_text, "outgoing")

            await _queue_product_images(
                db,
                phone,
                recommended_products,
                caption_mode="recommendation",
                failure_action="recommendation_image_send_failed",
            )

        await _queue_cross_sell_products(db, phone, query_text, recommended_products)
        _mark_processed(db, event, timing)
        return

    with timing.stage("shopify"):
        catalog_products = await find_cached_shopify_catalog_products(
            db,
            query_text,
            limit=requested_limit,
            entities=understanding.entities,
            phone=phone,
        )
    if not catalog_products:
        catalog_products = []
    if catalog_products:
        remember_last_products(db, phone, catalog_products)
        with timing.stage("whatsapp_send"):
            carousel_sent = await _try_send_product_carousel(
                phone,
                catalog_products,
                _localized(reply_language, "You can browse these catalog products.", "Catalog products dekh sakte hain."),
            )
        if carousel_sent:
            save_message(db, phone, "[carousel] Catalog", "outgoing")
            await _queue_cross_sell_products(db, phone, query_text, catalog_products)
            _mark_processed(db, event, timing)
            return
        with timing.stage("whatsapp_send"):
            product_list_sent = await _try_send_product_list(
                phone,
                catalog_products,
                "Catalog",
                _localized(reply_language, "You can browse these catalog products.", "Catalog products dekh sakte hain."),
            )
        if product_list_sent:
            save_message(db, phone, "[product_list] Catalog", "outgoing")
            await _queue_cross_sell_products(db, phone, query_text, catalog_products)
            _mark_processed(db, event, timing)
            return

        lines = ["Catalog:"]
        for index, product in enumerate(catalog_products, start=1):
            price = product.get("price_min") or ""
            if product.get("price_max") and product["price_max"] != product.get("price_min"):
                price = f"{product.get('price_min') or ''} - {product['price_max']}"
            product_line = f"{index}. {product['title']}"
            if price:
                product_line += f" - {price}"
            if product.get("product_url"):
                product_line += f"\n{product['product_url']}"
            lines.append(product_line)

        catalog_text = "\n\n".join(lines)
        with timing.stage("whatsapp_send"):
            await run_in_threadpool(send_whatsapp_message, phone, catalog_text)
        save_message(db, phone, catalog_text, "outgoing")

        await _queue_product_images(
            db,
            phone,
            catalog_products,
            caption_mode="caption",
            failure_action="catalog_image_send_failed",
        )

        await _queue_cross_sell_products(db, phone, query_text, catalog_products)
        _mark_processed(db, event, timing)
        return

    with timing.stage("shopify"):
        product_image = await find_cached_shopify_product_image(db, query_text, entities=understanding.entities, phone=phone)
    if not product_image:
        product_image = None
    if product_image:
        remember_last_products(db, phone, [product_image])
        with timing.stage("whatsapp_send"):
            cta_sent = await _try_send_product_cta(
                phone,
                product_image,
                "Buy now",
            )
        if cta_sent:
            save_message(db, phone, f"[cta_url] {product_image['title']}", "outgoing")
            await _queue_cross_sell_products(db, phone, query_text, [product_image])
            _mark_processed(db, event, timing)
            return
        with timing.stage("whatsapp_send"):
            product_list_sent = await _try_send_product_list(
                phone,
                [product_image],
                    "Product",
                    _localized(reply_language, "You can view this product detail.", "Product detail dekh sakte hain."),
                )
        if product_list_sent:
            save_message(db, phone, f"[product_list] {product_image['title']}", "outgoing")
            await _queue_cross_sell_products(db, phone, query_text, [product_image])
            _mark_processed(db, event, timing)
            return

        try:
            with timing.stage("whatsapp_send"):
                await run_in_threadpool(
                    send_whatsapp_image,
                    phone,
                    product_image["image_url"],
                    product_image["caption"],
                )
            save_message(db, phone, f"[image] {product_image['caption']}", "outgoing")
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=phone,
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
            db.commit()
            fallback_text = (
                _localized(
                    reply_language,
                    "I could not send the image, but here are the product details:\n",
                    "Image send nahi ho payi, lekin product detail yeh hai:\n",
                )
                + f"{product_image['caption']}"
            )
            with timing.stage("whatsapp_send"):
                await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
            save_message(db, phone, fallback_text, "outgoing")

        await _queue_cross_sell_products(db, phone, query_text, [product_image])
        _mark_processed(db, event, timing)
        return

    ai_reply = agent_state["reply_override"]
    if not ai_reply:
        if understanding.confidence >= 0.45 and understanding.tool:
            tool_decision = ToolDecision(understanding.tool, f"query_understanding:{understanding.intent}")
        else:
            tool_decision = decide_tool_for_message(query_text)
        with timing.stage("tool"):
            tool_result = run_ai_tool(db, phone, query_text, tool_decision)
        db.add(
            AgentAction(
                phone=phone,
                action_type="ai_tool_selected",
                status="logged",
                payload=json.dumps(
                    {
                        "message": text,
                        "normalized_query": query_text,
                        "tool": tool_result["tool"],
                        "reason": tool_result["reason"],
                    }
                ),
                result=json.dumps({"data_count": len(tool_result.get("data") or [])}),
            )
        )
        db.commit()
        try:
            with timing.stage("llm"):
                ai_reply = generate_ai_reply(
                    db,
                    phone,
                    text,
                    agent_context=_understanding_context(understanding, agent_state["context"]),
                    tool_context=tool_result["context"],
                )
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="ai_reply_failed_fallback_used",
                    status="failed",
                    payload=json.dumps({"message": text}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            ai_reply = bot_settings.fallback_message or (
                "I do not have that information right now. I can connect you with our support team."
            )
    with timing.stage("whatsapp_send"):
        await run_in_threadpool(send_whatsapp_message, phone, ai_reply)
    save_message(db, phone, ai_reply, "outgoing")
    _mark_processed(db, event, timing)


def _mark_processed(db: Session, event: WebhookEvent, timing: WebhookTiming | None = None) -> None:
    event.status = "processed"
    event.processed_at = datetime.utcnow()
    db.commit()
    if timing:
        timing.log("processed")


async def _try_mark_read_with_typing(db: Session, event: WebhookEvent) -> None:
    if not event.external_id:
        return
    try:
        await run_in_threadpool(mark_whatsapp_message_read_with_typing, event.external_id)
    except Exception as exc:
        db.add(
            AgentAction(
                phone=event.phone,
                action_type="typing_indicator_failed",
                status="failed",
                payload=json.dumps({"message_id": event.external_id}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()


async def _queue_cross_sell_products(
    db: Session,
    phone: str,
    text: str,
    base_products: list[dict],
) -> None:
    if not base_products:
        return
    try:
        await enqueue_whatsapp_cross_sell(phone, text, base_products)
    except Exception as exc:
        db.add(
            AgentAction(
                phone=phone,
                action_type="cross_sell_enqueue_failed",
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


def _active_handoff_ticket(db: Session, phone: str) -> HandoffTicket | None:
    if not phone:
        return None
    return db.execute(
        select(HandoffTicket)
        .where(HandoffTicket.phone == phone, HandoffTicket.status == "open")
        .order_by(HandoffTicket.updated_at.desc())
    ).scalars().first()


def _append_handoff_summary(db: Session, ticket: HandoffTicket, direction: str, message: str) -> None:
    line = f"{direction}: {message}".strip()
    ticket.summary = "\n".join(filter(None, [ticket.summary, line]))[-5000:]
    ticket.updated_at = datetime.utcnow()
    db.commit()


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
    from app.modules.ecommerce.core.shopify_cache_service import _active_shopify_connection

    return _active_shopify_connection(db, phone=phone)


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

