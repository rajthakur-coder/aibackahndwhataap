import json
from datetime import datetime

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.entities import AgentAction, WebhookEvent
from app.services.agent import process_agent_message
from app.services.conversation_memory import remember_last_products, remember_last_question
from app.services.messages import save_message
from app.services.openai_chat import generate_ai_reply
from app.services.rag import (
    find_relevant_catalog_products,
    find_relevant_product_image,
    find_relevant_website_images,
)
from app.services.sales_recommendations import (
    find_product_recommendations,
    recommendation_caption,
    recommendation_intro,
)
from app.services.whatsapp import send_whatsapp_image, send_whatsapp_message


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
        event = db.query(WebhookEvent).filter(WebhookEvent.external_id == external_id).first()
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
        save_message(db, phone, text, "incoming")
        remember_last_question(db, phone, text)

    agent_state = process_agent_message(db, phone, text)
    recommended_products = find_product_recommendations(db, text, limit=3)
    if recommended_products:
        remember_last_products(db, phone, recommended_products)
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

        _mark_processed(db, event)
        return

    catalog_products = find_relevant_catalog_products(db, text, limit=3)
    if catalog_products:
        remember_last_products(db, phone, catalog_products)
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

        _mark_processed(db, event)
        return

    product_image = find_relevant_product_image(db, text)
    if product_image:
        remember_last_products(db, phone, [product_image])
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

        _mark_processed(db, event)
        return

    website_images = find_relevant_website_images(db, text, limit=2)
    if website_images:
        remember_last_products(db, phone, website_images)
        sent_count = 0
        failed_images = []
        for website_image in website_images:
            try:
                await run_in_threadpool(
                    send_whatsapp_image,
                    phone,
                    website_image["image_url"],
                    website_image["caption"],
                )
                save_message(db, phone, f"[image] {website_image['caption']}", "outgoing")
                sent_count += 1
            except Exception as exc:
                failed_images.append(website_image)
                db.add(
                    AgentAction(
                        phone=phone,
                        action_type="website_image_send_failed",
                        status="failed",
                        payload=json.dumps(
                            {
                                "title": website_image["title"],
                                "page_url": website_image["page_url"],
                                "image_url": website_image["image_url"],
                            }
                        ),
                        result=json.dumps({"error": str(exc)}),
                    )
                )
                db.commit()

        if sent_count == 0:
            fallback_lines = ["Image send nahi ho payi, yeh image links dekh sakte hain:"]
            for image in failed_images[:3]:
                fallback_lines.append(f"{image['title']}\n{image['image_url']}")
            fallback_text = "\n\n".join(fallback_lines)
            await run_in_threadpool(send_whatsapp_message, phone, fallback_text)
            save_message(db, phone, fallback_text, "outgoing")

        _mark_processed(db, event)
        return

    ai_reply = agent_state["reply_override"] or generate_ai_reply(
        db,
        phone,
        text,
        agent_context=agent_state["context"],
    )
    await run_in_threadpool(send_whatsapp_message, phone, ai_reply)
    save_message(db, phone, ai_reply, "outgoing")
    _mark_processed(db, event)


def _mark_processed(db: Session, event: WebhookEvent) -> None:
    event.status = "processed"
    event.processed_at = datetime.utcnow()
    db.commit()
