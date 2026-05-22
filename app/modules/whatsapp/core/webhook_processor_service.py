import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction
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
CATALOG_CATEGORY_ROWS = [
    {"id": "catalog:all", "title": "All products", "description": "Browse the full catalog"},
    {"id": "catalog:best_sellers", "title": "Best sellers", "description": "Popular products"},
]
CATALOG_CATEGORY_LABELS = {
    "all": "All products",
    "best_sellers": "Best sellers",
}
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
                    if reply_id.startswith("catalog:category:"):
                        text = f"catalog dynamic category {reply_id.removeprefix('catalog:category:')} {title}".strip()
                    elif reply_id.startswith("catalog:"):
                        text = f"catalog category {reply_id.removeprefix('catalog:')} {title}".strip()
                    else:
                        text = title or reply_id
                elif button_reply:
                    reply_id = str(button_reply.get("id") or "")
                    title = str(button_reply.get("title") or "")
                    if reply_id == "menu:catalog":
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
    if _is_main_menu_request(query_text):
        menu_sent = await _try_send_main_menu(phone)
        if menu_sent:
            save_message(db, phone, "[buttons] Main menu", "outgoing")
            _mark_processed(db, event)
            return

    selected_catalog_category = _selected_catalog_category(query_text)
    if selected_catalog_category:
        category_products = await _products_for_catalog_category(db, selected_catalog_category, requested_limit)
        if category_products:
            remember_last_products(db, phone, category_products)
            label_key = selected_catalog_category.removeprefix("dynamic:")
            label = CATALOG_CATEGORY_LABELS.get(selected_catalog_category, label_key.replace("_", " ").title())
            carousel_sent = await _try_send_product_carousel(
                phone,
                category_products,
                f"{label} products dekh sakte hain.",
            )
            if carousel_sent:
                save_message(db, phone, f"[carousel] {label}", "outgoing")
                _mark_processed(db, event)
                return
            product_list_sent = await _try_send_product_list(
                phone,
                category_products,
                label,
                f"{label} products dekh sakte hain.",
            )
            if product_list_sent:
                save_message(db, phone, f"[product_list] {label}", "outgoing")
                _mark_processed(db, event)
                return
        fallback_text = "Is category me abhi products nahi mile. Aap All products try kar sakte hain."
        await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
        save_message(db, phone, fallback_text, "outgoing")
        _mark_processed(db, event)
        return

    if _looks_like_catalog_request(query_text):
        category_list_sent = await _try_send_catalog_category_list(db, phone)
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
                "Ye top-selling products hain.",
            )
            if carousel_sent:
                save_message(db, phone, "[carousel] Top selling products", "outgoing")
                _mark_processed(db, event)
                return
            product_list_sent = await _try_send_product_list(
                phone,
                top_selling_products,
                "Top selling products",
                "Ye top-selling products hain.",
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
            recommendation_text = "Abhi top-selling products nikalne ke liye order/sales data available nahi hai."
            await run_in_threadpool(send_whatsapp_message, phone, recommendation_text)
            save_message(db, phone, recommendation_text, "outgoing")

        _mark_processed(db, event)
        return

    recommended_products = await find_cached_shopify_product_recommendations(
        db,
        query_text,
        limit=requested_limit,
    )
    if recommended_products:
        remember_last_products(db, phone, recommended_products)
        carousel_sent = await _try_send_product_carousel(
            phone,
            recommended_products,
            "Aapke liye matching products.",
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
            "Aapke liye matching products.",
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
    )
    if not catalog_products:
        catalog_products = []
    if catalog_products:
        remember_last_products(db, phone, catalog_products)
        carousel_sent = await _try_send_product_carousel(
            phone,
            catalog_products,
            "Catalog products dekh sakte hain.",
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
            "Catalog products dekh sakte hain.",
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

    product_image = await find_cached_shopify_product_image(db, query_text)
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
            "Product detail dekh sakte hain.",
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
                "Image send nahi ho payi, lekin product detail yeh hai:\n"
                f"{product_image['caption']}"
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
    normalized = " ".join((query or "").lower().split())
    return normalized in GREETING_TERMS


async def _try_send_main_menu(phone: str) -> bool:
    try:
        await run_in_threadpool(
            send_whatsapp_reply_buttons,
            phone,
            "Kaise help kar sakte hain?",
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


async def _products_for_catalog_category(db: Session, category: str, limit: int) -> list[dict]:
    if category == "best_sellers":
        return await find_cached_shopify_top_selling_products(db, limit=limit)
    if category == "all":
        return await find_cached_shopify_category_products(db, "all", limit=limit)
    if category.startswith("dynamic:"):
        return await find_cached_shopify_category_products(db, category.removeprefix("dynamic:"), limit=limit)
    return await find_cached_shopify_category_products(db, category, limit=limit)


async def _try_send_catalog_category_list(db: Session, phone: str) -> bool:
    dynamic_rows = await find_cached_shopify_catalog_categories(db, limit=8)
    rows = list(CATALOG_CATEGORY_ROWS)
    seen_ids = {row["id"] for row in rows}
    for row in dynamic_rows:
        if row.get("id") not in seen_ids:
            rows.append(row)
            seen_ids.add(row["id"])
        if len(rows) >= 10:
            break
    try:
        await run_in_threadpool(
            send_whatsapp_list,
            phone,
            "Kaunsi category dekhni hai?",
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
    return
