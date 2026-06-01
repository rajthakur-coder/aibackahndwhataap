import json
import re

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction
from app.modules.ai.recommendations.sales_recommendations_service import recommendation_caption
from app.modules.ai.recommendations.sales_recommendations_service import extract_requested_limit
from app.modules.ecommerce.catalog.catalog_cache_service import (
    find_cached_catalog_categories,
    find_cached_category_products,
    find_cached_cross_sell_products,
    find_cached_top_selling_products,
)
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.client.client_service import (
    send_whatsapp_carousel,
    send_whatsapp_cta_url,
    send_whatsapp_image,
    send_whatsapp_list,
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


from app.modules.whatsapp.webhooks.responses.intent_service import *

async def try_send_product_list(
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

async def try_send_product_carousel(
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

async def try_send_product_cta(
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

async def send_cross_sell_products(
    db: Session,
    phone: str,
    text: str,
    base_products: list[dict],
) -> None:
    products = await find_cached_cross_sell_products(db, text, base_products, limit=3, phone=phone)
    if not products:
        return
    sent = await try_send_product_carousel(
        phone,
        products,
        localized(reply_language(text), "You may also like these.", "Aapko ye bhi pasand aa sakta hai."),
    )
    if sent:
        save_message(
            db,
            phone,
            "[carousel] Cross-sell products",
            "outgoing",
            message_type="carousel",
            payload={
                "body": localized(reply_language(text), "You may also like these.", "Aapko ye bhi pasand aa sakta hai."),
                "products": product_cards_payload(products),
            },
        )

async def send_product_images(
    db: Session,
    phone: str,
    products: list[dict],
    caption_mode: str = "caption",
    failure_action: str = "product_image_send_failed",
) -> None:
    for product in products[:2]:
        image_url = product.get("image_url")
        if not image_url:
            continue
        try:
            caption = (
                recommendation_caption(product)
                if caption_mode == "recommendation"
                else product.get("caption") or recommendation_caption(product)
            )
            await run_in_threadpool(send_whatsapp_image, phone, image_url, caption)
            save_message(
                db,
                phone,
                f"[image] {caption}",
                "outgoing",
                message_type="image",
                payload={
                    "caption": caption,
                    "image_url": image_url,
                    "title": product.get("title") or "Product",
                    "product_url": product.get("product_url"),
                },
            )
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=phone,
                    action_type=failure_action,
                    status="failed",
                    payload=json.dumps(
                        {
                            "title": product.get("title"),
                            "image_url": image_url,
                        }
                    ),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()

def product_cards_payload(products: list[dict]) -> list[dict]:
    return [
        {
            "title": product.get("title") or "Product",
            "price": product.get("price") or product.get("price_min") or product.get("price_max") or "",
            "image_url": product.get("image_url"),
            "product_url": product.get("product_url"),
            "caption": product.get("caption") or product.get("description") or "",
        }
        for product in products[:10]
    ]

__all__ = [
    "try_send_product_list",
    "try_send_product_carousel",
    "try_send_product_cta",
    "send_cross_sell_products",
    "send_product_images",
    "product_cards_payload",
]
