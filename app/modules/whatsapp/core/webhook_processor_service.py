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
    find_cached_shopify_order_status,
    find_cached_shopify_product_image,
    find_cached_shopify_product_recommendations,
    find_cached_shopify_top_selling_products,
)
from app.modules.whatsapp.core.whatsapp_client_service import (
    send_whatsapp_carousel,
    send_whatsapp_image,
    send_whatsapp_message,
    send_whatsapp_product_list,
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


def parse_whatsapp_messages(payload: dict) -> list[dict]:
    parsed_messages = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                message_id = message.get("id")
                text = message.get("text", {}).get("body")
                phone = message.get("from")

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


async def _send_cross_sell_products(
    db: Session,
    phone: str,
    text: str,
    base_products: list[dict],
) -> None:
    return
