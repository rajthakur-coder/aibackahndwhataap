import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction, HandoffTicket
from app.models.whatsapp import WebhookEvent
from app.modules.crm.core.crm_agent_service import process_agent_message
from app.modules.ai.core.ai_tools_service import ToolDecision, decide_tool_for_message, run_ai_tool
from app.modules.crm.core.conversation_memory_service import remember_last_products, remember_last_question
from app.modules.whatsapp.core.messages_service import save_message
from app.modules.whatsapp.core.live_chat_service import serialize_message
from app.modules.whatsapp.core.live_chat_socket import live_chat_manager
from app.modules.ai.core.openai_chat_service import generate_ai_reply
from app.modules.ai.core.query_understanding_service import understand_message
from app.modules.ai.core.sales_recommendations_service import (
    extract_requested_limit,
    is_top_selling_request,
    recommendation_caption,
    recommendation_intro,
)
from app.modules.ecommerce.core.shopify_cache_service import (
    find_cached_shopify_catalog_products,
    find_cached_shopify_catalog_categories,
    find_cached_shopify_category_products,
    find_cached_shopify_cross_sell_products,
    find_cached_shopify_order_status,
    find_cached_shopify_product_image,
    find_cached_shopify_product_recommendations,
    find_cached_shopify_top_selling_products,
)
from app.modules.whatsapp.core.whatsapp_client_service import (
    send_whatsapp_carousel,
    send_whatsapp_cta_url,
    send_whatsapp_image,
    send_whatsapp_list,
    send_whatsapp_message,
    send_whatsapp_product_list,
    send_whatsapp_reply_buttons,
)


IMAGE_REQUEST_TERMS = {
    "image",
    "images",
    "photo",
    "photos",
    "pic",
    "picture",
    "tasveer",
    "tasvir",
    "dikha",
    "dikhana",
    "dikhao",
    "bhejo",
}
CATALOG_REQUEST_TERMS = {"catalog", "catalogue", "products", "product", "collection", "items", "list", "menu"}
REQUEST_ACTION_TERMS = {"bhejo", "chahiye", "chaiye", "dekhna", "dikha", "dikhana", "dikhao", "send", "show"}
HINGLISH_TERMS = {
    "aap",
    "abhi",
    "batao",
    "bhejo",
    "chahiye",
    "chaiye",
    "dekhna",
    "dikha",
    "dikhana",
    "dikhao",
    "hai",
    "hain",
    "kaise",
    "karo",
    "kya",
    "mera",
    "mere",
    "mujhe",
    "nahi",
    "shai",
}
CATALOG_CATEGORY_ROWS = [
    {"id": "catalog:all", "title": "All products", "description": "Browse the full catalog"},
    {"id": "catalog:best_sellers", "title": "Best sellers", "description": "Popular products"},
]
CATALOG_CATEGORY_LABELS = {
    "all": "All products",
    "best_sellers": "Best sellers",
}
CATALOG_PAGE_SIZE = 8
MAIN_MENU_BUTTONS = [
    {"id": "menu:catalog", "title": "View catalog"},
    {"id": "menu:order_status", "title": "Track order"},
    {"id": "menu:human", "title": "Talk to human"},
]
GREETING_TERMS = {"hi", "hello", "hey", "menu", "help", "start", "namaste", "hii"}


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
    return event.status == "failed"


def mark_webhook_event_failed(db: Session, event: WebhookEvent, exc: Exception) -> None:
    event.status = "failed"
    event.error = str(exc)
    db.commit()


async def process_webhook_event(event: WebhookEvent, db: Session) -> None:
    phone = event.phone or ""
    text = event.message_text or ""
    attempt_number = (event.attempts or 0) + 1
    event.attempts = attempt_number
    event.status = "processing"
    event.error = None
    db.commit()

    if attempt_number == 1:
        incoming_row = save_message(db, phone, text, "incoming")
        await live_chat_manager.broadcast(
            {
                "type": "live_chat_message",
                "direction": "in",
                "contact": phone,
                "message": serialize_message(incoming_row),
            }
        )
        remember_last_question(db, phone, text)

    active_handoff = _active_handoff_ticket(db, phone)
    if active_handoff:
        _append_handoff_summary(db, active_handoff, "incoming", text)
        handoff_text = _localized(
            _reply_language(text),
            f"Your request is already with our support team. Ticket #{active_handoff.id} is open, and they will reply shortly.",
            f"Aapki request support team ke paas hai. Ticket #{active_handoff.id} open hai, team jaldi reply karegi.",
        )
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
        _mark_processed(db, event)
        return

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

    agent_state = process_agent_message(db, phone, query_text)
    if agent_state["intent"] == "order_status":
        shopify_order_reply = await find_cached_shopify_order_status(db, phone, query_text)
        if shopify_order_reply:
            agent_state["reply_override"] = shopify_order_reply
    requested_limit = _requested_limit_from_understanding(understanding, query_text)
    reply_language = _reply_language(query_text)
    if understanding.intent in {"greeting", "menu_request"} or _is_main_menu_request(query_text):
        menu_sent = await _try_send_main_menu(phone, reply_language)
        if menu_sent:
            save_message(db, phone, "[buttons] Main menu", "outgoing")
            _mark_processed(db, event)
            return

    selected_catalog_category = _selected_catalog_category(query_text)
    if selected_catalog_category:
        category_page = _selected_catalog_product_page(query_text)
        category_fetch_limit = requested_limit + 1
        category_offset = (category_page - 1) * requested_limit
        category_products_page = await _products_for_catalog_category(
            db,
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
            carousel_sent = await _try_send_product_carousel(
                phone,
                category_products,
                _localized(reply_language, f"You can browse {label} products.", f"{label} products dekh sakte hain."),
            )
            if carousel_sent:
                save_message(db, phone, f"[carousel] {label}", "outgoing")
                if has_more_category_products:
                    await _try_send_category_more_button(
                        phone,
                        selected_catalog_category,
                        category_page + 1,
                        label,
                        reply_language,
                    )
                _mark_processed(db, event)
                return
            product_list_sent = await _try_send_product_list(
                phone,
                category_products,
                label,
                _localized(reply_language, f"You can browse {label} products.", f"{label} products dekh sakte hain."),
            )
            if product_list_sent:
                save_message(db, phone, f"[product_list] {label}", "outgoing")
                if has_more_category_products:
                    await _try_send_category_more_button(
                        phone,
                        selected_catalog_category,
                        category_page + 1,
                        label,
                        reply_language,
                    )
                _mark_processed(db, event)
                return
        fallback_text = _localized(
            reply_language,
            "No products found in this category right now. You can try All products.",
            "Is category me abhi products nahi mile. Aap All products try kar sakte hain.",
        )
        await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
        save_message(db, phone, fallback_text, "outgoing")
        _mark_processed(db, event)
        return

    if _looks_like_catalog_request(query_text):
        category_list_sent = await _try_send_catalog_category_list(
            db,
            phone,
            reply_language,
            page=_catalog_page_number(query_text),
        )
        if category_list_sent:
            save_message(db, phone, "[list] Catalog categories", "outgoing")
            _mark_processed(db, event)
            return

    if is_top_selling_request(query_text) or understanding.intent == "top_selling_products":
        top_selling_products = await find_cached_shopify_top_selling_products(db, limit=requested_limit)
        if top_selling_products:
            remember_last_products(db, phone, top_selling_products)
            carousel_sent = await _try_send_product_carousel(
                phone,
                top_selling_products,
                _localized(reply_language, "These are the top-selling products.", "Ye top-selling products hain."),
            )
            if carousel_sent:
                save_message(db, phone, "[carousel] Top selling products", "outgoing")
                _mark_processed(db, event)
                return
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
                await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
                save_message(db, phone, recommendation_text, "outgoing")

                for product in top_selling_products[:2]:
                    if not product.get("image_url"):
                        continue
                    try:
                        caption = recommendation_caption(product)
                        await run_in_threadpool(
                            send_whatsapp_image,
                            phone,
                            product["image_url"],
                            caption,
                        )
                        save_message(db, phone, f"[image] {caption}", "outgoing")
                    except Exception as exc:
                        db.add(
                            AgentAction(
                                phone=phone,
                                action_type="top_selling_image_send_failed",
                                status="failed",
                                payload=json.dumps(
                                    {
                                        "title": product["title"],
                                        "image_url": product["image_url"],
                                    }
                                ),
                                result=json.dumps({"error": str(exc)}),
                            )
                        )
                        db.commit()
        else:
            recommendation_text = _localized(
                reply_language,
                "Sales data is not available yet to calculate top-selling products.",
                "Abhi top-selling products nikalne ke liye order/sales data available nahi hai.",
            )
            await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
            save_message(db, phone, recommendation_text, "outgoing")

        _mark_processed(db, event)
        return

    recommended_products = await find_cached_shopify_product_recommendations(
        db,
        query_text,
        limit=requested_limit,
        entities=understanding.entities,
    )
    if recommended_products:
        remember_last_products(db, phone, recommended_products)
        carousel_sent = await _try_send_product_carousel(
            phone,
            recommended_products,
            _localized(reply_language, "Matching products for you.", "Aapke liye matching products."),
        )
        if carousel_sent:
            save_message(db, phone, "[carousel] Recommended products", "outgoing")
            await _send_cross_sell_products(db, phone, query_text, recommended_products)
            _mark_processed(db, event)
            return
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
            await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
            save_message(db, phone, recommendation_text, "outgoing")

            for product in recommended_products[:2]:
                if not product.get("image_url"):
                    continue
                try:
                    caption = recommendation_caption(product)
                    await run_in_threadpool(
                        send_whatsapp_image,
                        phone,
                        product["image_url"],
                        caption,
                    )
                    save_message(db, phone, f"[image] {caption}", "outgoing")
                except Exception as exc:
                    db.add(
                        AgentAction(
                            phone=phone,
                            action_type="recommendation_image_send_failed",
                            status="failed",
                            payload=json.dumps(
                                {
                                    "title": product["title"],
                                    "image_url": product["image_url"],
                                }
                            ),
                            result=json.dumps({"error": str(exc)}),
                        )
                    )
                    db.commit()

        await _send_cross_sell_products(db, phone, query_text, recommended_products)
        _mark_processed(db, event)
        return

    catalog_products = await find_cached_shopify_catalog_products(
        db,
        query_text,
        limit=requested_limit,
        entities=understanding.entities,
    )
    if not catalog_products:
        catalog_products = []
    if catalog_products:
        remember_last_products(db, phone, catalog_products)
        carousel_sent = await _try_send_product_carousel(
            phone,
            catalog_products,
            _localized(reply_language, "You can browse these catalog products.", "Catalog products dekh sakte hain."),
        )
        if carousel_sent:
            save_message(db, phone, "[carousel] Catalog", "outgoing")
            await _send_cross_sell_products(db, phone, query_text, catalog_products)
            _mark_processed(db, event)
            return
        product_list_sent = await _try_send_product_list(
            phone,
            catalog_products,
            "Catalog",
            _localized(reply_language, "You can browse these catalog products.", "Catalog products dekh sakte hain."),
        )
        if product_list_sent:
            save_message(db, phone, "[product_list] Catalog", "outgoing")
            await _send_cross_sell_products(db, phone, query_text, catalog_products)
            _mark_processed(db, event)
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
        await run_in_threadpool(send_whatsapp_message, phone, catalog_text)
        save_message(db, phone, catalog_text, "outgoing")

        for product in catalog_products[:2]:
            if not product.get("image_url"):
                continue
            try:
                await run_in_threadpool(
                    send_whatsapp_image,
                    phone,
                    product["image_url"],
                    product["caption"],
                )
                save_message(db, phone, f"[image] {product['caption']}", "outgoing")
            except Exception as exc:
                db.add(
                    AgentAction(
                        phone=phone,
                        action_type="catalog_image_send_failed",
                        status="failed",
                        payload=json.dumps(
                            {
                                "title": product["title"],
                                "image_url": product["image_url"],
                            }
                        ),
                        result=json.dumps({"error": str(exc)}),
                    )
                )
                db.commit()

        await _send_cross_sell_products(db, phone, query_text, catalog_products)
        _mark_processed(db, event)
        return

    product_image = await find_cached_shopify_product_image(db, query_text, entities=understanding.entities)
    if not product_image:
        product_image = None
    if product_image:
        remember_last_products(db, phone, [product_image])
        cta_sent = await _try_send_product_cta(
            phone,
            product_image,
            "Buy now",
        )
        if cta_sent:
            save_message(db, phone, f"[cta_url] {product_image['title']}", "outgoing")
            await _send_cross_sell_products(db, phone, query_text, [product_image])
            _mark_processed(db, event)
            return
        product_list_sent = await _try_send_product_list(
            phone,
            [product_image],
                "Product",
                _localized(reply_language, "You can view this product detail.", "Product detail dekh sakte hain."),
            )
        if product_list_sent:
            save_message(db, phone, f"[product_list] {product_image['title']}", "outgoing")
            await _send_cross_sell_products(db, phone, query_text, [product_image])
            _mark_processed(db, event)
            return

        try:
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
            await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
            save_message(db, phone, fallback_text, "outgoing")

        await _send_cross_sell_products(db, phone, query_text, [product_image])
        _mark_processed(db, event)
        return

    ai_reply = agent_state["reply_override"]
    if not ai_reply:
        if understanding.confidence >= 0.45 and understanding.tool:
            tool_decision = ToolDecision(understanding.tool, f"query_understanding:{understanding.intent}")
        else:
            tool_decision = decide_tool_for_message(query_text)
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
        ai_reply = generate_ai_reply(
            db,
            phone,
            text,
            agent_context=_understanding_context(understanding, agent_state["context"]),
            tool_context=tool_result["context"],
        )
    await run_in_threadpool(send_whatsapp_message, phone, ai_reply)
    save_message(db, phone, ai_reply, "outgoing")
    _mark_processed(db, event)


def _mark_processed(db: Session, event: WebhookEvent) -> None:
    event.status = "processed"
    event.processed_at = datetime.utcnow()
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


def _requested_limit_from_understanding(understanding, query_text: str) -> int:
    try:
        limit = int(float(understanding.entities.get("limit") or 0))
    except (TypeError, ValueError):
        limit = 0
    return limit or extract_requested_limit(query_text, default=5)


def _understanding_context(understanding, agent_context: str) -> str:
    parts = [
        f"Normalized user query: {understanding.normalized_query}",
        f"Detected intent: {understanding.intent}",
        f"Confidence: {understanding.confidence:.2f}",
    ]
    if understanding.entities:
        parts.append(f"Entities: {json.dumps(understanding.entities, ensure_ascii=True)}")
    if agent_context:
        parts.append(agent_context)
    return "\n".join(parts)


def _looks_like_catalog_request(query: str) -> bool:
    terms = _request_terms(query)
    return bool(terms & CATALOG_REQUEST_TERMS and terms & REQUEST_ACTION_TERMS)


def _looks_like_image_request(query: str) -> bool:
    return bool(_request_terms(query) & IMAGE_REQUEST_TERMS)


def _request_terms(query: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9]+", query or "")}


def _is_main_menu_request(query: str) -> bool:
    tokens = [_squash_repeated_letters(token.lower()) for token in re.findall(r"[a-zA-Z0-9]+", query or "")]
    if not tokens:
        return False
    if any(token in {"menu", "help", "start"} for token in tokens[:4]):
        return True
    if tokens[0] not in GREETING_TERMS:
        return False
    intent_words = {"order", "track", "product", "products", "catalog", "price", "image", "status"}
    return len(tokens) <= 4 and not bool(set(tokens[1:]) & intent_words)


def _squash_repeated_letters(value: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", value or "")


def _reply_language(query: str) -> str:
    terms = _request_terms(query)
    return "hinglish" if terms & HINGLISH_TERMS else "english"


def _localized(language: str, english: str, hinglish: str) -> str:
    return hinglish if language == "hinglish" else english


async def _try_send_main_menu(phone: str, language: str = "english") -> bool:
    try:
        await run_in_threadpool(
            send_whatsapp_reply_buttons,
            phone,
            _localized(language, "How can I help you?", "Kaise help kar sakte hain?"),
            MAIN_MENU_BUTTONS,
            "Main menu",
        )
    except Exception:
        return False
    return True


def _selected_catalog_category(query: str) -> str | None:
    normalized = " ".join((query or "").lower().split())
    dynamic_match = re.search(r"\bcatalog dynamic category ([a-z0-9_]+)\b", normalized)
    if dynamic_match:
        return f"dynamic:{dynamic_match.group(1)}"
    match = re.search(r"\bcatalog category ([a-z_]+)\b", normalized)
    if not match:
        return None
    category = match.group(1)
    return category if category in CATALOG_CATEGORY_LABELS else None


def _selected_catalog_product_page(query: str) -> int:
    match = re.search(r"\bpage (\d+)\b", " ".join((query or "").lower().split()))
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


async def _products_for_catalog_category(
    db: Session,
    category: str,
    limit: int,
    offset: int = 0,
) -> list[dict]:
    if category == "best_sellers":
        return await find_cached_shopify_top_selling_products(db, limit=limit)
    if category == "all":
        return await find_cached_shopify_category_products(db, "all", limit=limit, offset=offset)
    if category.startswith("dynamic:"):
        return await find_cached_shopify_category_products(
            db,
            category.removeprefix("dynamic:"),
            limit=limit,
            offset=offset,
        )
    return await find_cached_shopify_category_products(db, category, limit=limit, offset=offset)


async def _try_send_category_more_button(
    phone: str,
    category: str,
    next_page: int,
    label: str,
    language: str,
) -> bool:
    category_key = category.removeprefix("dynamic:")
    try:
        await run_in_threadpool(
            send_whatsapp_reply_buttons,
            phone,
            _localized(language, f"Do you want to see more {label} products?", f"Aur {label} products dekhne hain?"),
            [{"id": f"catalog:more:{category_key}:{next_page}", "title": "Show more"}],
        )
    except Exception:
        return False
    return True


def _catalog_page_number(query: str) -> int:
    match = re.search(r"\bcatalog page (\d+)\b", " ".join((query or "").lower().split()))
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


async def _try_send_catalog_category_list(
    db: Session,
    phone: str,
    language: str = "english",
    page: int = 1,
) -> bool:
    page = max(1, page)
    dynamic_rows = await find_cached_shopify_catalog_categories(db, limit=50)
    rows = []
    if page == 1:
        rows.extend(CATALOG_CATEGORY_ROWS)

    first_page_dynamic_slots = 9 - len(CATALOG_CATEGORY_ROWS)
    start = 0 if page == 1 else first_page_dynamic_slots + ((page - 2) * CATALOG_PAGE_SIZE)
    if page == 1:
        end = start + first_page_dynamic_slots
    else:
        end = start + CATALOG_PAGE_SIZE
    rows.extend(dynamic_rows[start:end])

    if end < len(dynamic_rows) and len(rows) < 10:
        rows.append(
            {
                "id": f"catalog:page:{page + 1}",
                "title": "Next categories",
                "description": "Show more categories",
            }
        )
    if page > 1 and len(rows) < 10:
        rows.append(
            {
                "id": f"catalog:page:{page - 1}",
                "title": "Previous categories",
                "description": "Go back",
            }
        )
    if not rows:
        return False
    try:
        await run_in_threadpool(
            send_whatsapp_list,
            phone,
            _localized(language, "Which category would you like to view?", "Kaunsi category dekhni hai?"),
            "Categories",
            rows,
            "Catalog",
            "Choose category",
        )
    except Exception:
        return False
    return True


async def _try_send_product_list(
    phone: str,
    products: list[dict],
    header_text: str,
    body_text: str,
) -> bool:
    try:
        await run_in_threadpool(
            send_whatsapp_product_list,
            phone,
            products,
            body_text,
            header_text,
            "Products",
        )
    except Exception:
        return False
    return True


async def _try_send_product_carousel(
    phone: str,
    products: list[dict],
    body_text: str,
) -> bool:
    try:
        await run_in_threadpool(
            send_whatsapp_carousel,
            phone,
            products,
            body_text,
            "Buy now",
        )
    except Exception:
        return False
    return True


async def _try_send_product_cta(
    phone: str,
    product: dict,
    button_text: str,
) -> bool:
    product_url = product.get("product_url")
    if not product_url:
        return False

    title = str(product.get("title") or "Product").strip()
    price = str(product.get("price") or product.get("price_min") or "").strip()
    body_parts = [title]
    if price:
        body_parts.append(f"Price: {price}")
    description = str(product.get("description") or "").strip()
    if description and description != title:
        body_parts.append(description[:180])

    try:
        await run_in_threadpool(
            send_whatsapp_cta_url,
            phone,
            "\n".join(body_parts),
            button_text,
            product_url,
            title,
            product.get("image_url"),
        )
    except Exception:
        return False
    return True


async def _send_cross_sell_products(
    db: Session,
    phone: str,
    text: str,
    base_products: list[dict],
) -> None:
    products = await find_cached_shopify_cross_sell_products(db, text, base_products, limit=3)
    if not products:
        return
    sent = await _try_send_product_carousel(
        phone,
        products,
        _localized(_reply_language(text), "You may also like these.", "Aapko ye bhi pasand aa sakta hai."),
    )
    if sent:
        save_message(db, phone, "[carousel] Cross-sell products", "outgoing")
