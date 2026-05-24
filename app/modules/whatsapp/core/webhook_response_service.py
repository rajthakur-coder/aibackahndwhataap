import json
import re

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.models.crm import AgentAction
from app.modules.ai.core.sales_recommendations_service import recommendation_caption
from app.modules.ai.core.sales_recommendations_service import extract_requested_limit
from app.modules.ecommerce.core.shopify_cache_service import (
    find_cached_shopify_catalog_categories,
    find_cached_shopify_category_products,
    find_cached_shopify_cross_sell_products,
    find_cached_shopify_top_selling_products,
)
from app.modules.whatsapp.core.messages_service import save_message
from app.modules.whatsapp.core.whatsapp_client_service import (
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


def requested_limit_from_understanding(understanding, query_text: str) -> int:
    try:
        limit = int(float(understanding.entities.get("limit") or 0))
    except (TypeError, ValueError):
        limit = 0
    return limit or extract_requested_limit(query_text, default=5)


def understanding_context(understanding, agent_context: str) -> str:
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


def looks_like_catalog_request(query: str) -> bool:
    terms = request_terms(query)
    return bool(terms & CATALOG_REQUEST_TERMS and terms & REQUEST_ACTION_TERMS)


def is_catalog_page_request(query: str) -> bool:
    normalized = " ".join((query or "").lower().split())
    if re.search(r"\bcatalog page \d+\b", normalized):
        return True
    terms = request_terms(normalized)
    return bool(
        {"catalog", "categories"} <= terms
        and terms & {"next", "more", "previous", "back", "show"}
    )


def looks_like_image_request(query: str) -> bool:
    return bool(request_terms(query) & IMAGE_REQUEST_TERMS)


def request_terms(query: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[a-zA-Z0-9]+", query or "")}


def is_main_menu_request(query: str) -> bool:
    tokens = [squash_repeated_letters(token.lower()) for token in re.findall(r"[a-zA-Z0-9]+", query or "")]
    if not tokens:
        return False
    if any(token in {"menu", "help", "start"} for token in tokens[:4]):
        return True
    if tokens[0] not in GREETING_TERMS:
        return False
    intent_words = {"order", "track", "product", "products", "catalog", "price", "image", "status"}
    return len(tokens) <= 4 and not bool(set(tokens[1:]) & intent_words)


def squash_repeated_letters(value: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", value or "")


def reply_language(query: str, bot_settings=None) -> str:
    default_language = str(getattr(bot_settings, "default_language", "auto") or "auto").strip().lower()
    if default_language in {"english", "hinglish", "hindi"}:
        return default_language
    terms = request_terms(query)
    return "hinglish" if terms & HINGLISH_TERMS else "english"


def localized(language: str, english: str, hinglish: str) -> str:
    return hinglish if language in {"hinglish", "hindi"} else english


def main_menu_buttons(bot_settings=None) -> list[dict]:
    try:
        buttons = json.loads(getattr(bot_settings, "main_menu_buttons", "") or "[]")
    except json.JSONDecodeError:
        buttons = []
    clean_buttons = [
        {"id": str(button.get("id") or "").strip(), "title": str(button.get("title") or "").strip()[:20]}
        for button in buttons
        if isinstance(button, dict) and button.get("id") and button.get("title")
    ]
    return clean_buttons[:3] or MAIN_MENU_BUTTONS


async def try_send_main_menu(phone: str, language: str = "english", bot_settings=None) -> bool:
    try:
        await run_in_threadpool(
            send_whatsapp_reply_buttons,
            phone,
            getattr(bot_settings, "welcome_message", None)
            or localized(language, "How can I help you?", "Kaise help kar sakte hain?"),
            main_menu_buttons(bot_settings),
            "Main menu",
        )
    except Exception:
        return False
    return True


def selected_catalog_category(query: str) -> str | None:
    normalized = " ".join((query or "").lower().split())
    dynamic_match = re.search(r"\bcatalog dynamic category ([a-z0-9_]+)\b", normalized)
    if dynamic_match:
        return f"dynamic:{dynamic_match.group(1)}"
    match = re.search(r"\bcatalog category ([a-z_]+)\b", normalized)
    if not match:
        return None
    category = match.group(1)
    return category if category in CATALOG_CATEGORY_LABELS else None


def selected_catalog_product_page(query: str) -> int:
    match = re.search(r"\bpage (\d+)\b", " ".join((query or "").lower().split()))
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


async def products_for_catalog_category(
    db: Session,
    phone: str,
    category: str,
    limit: int,
    offset: int = 0,
) -> list[dict]:
    if category == "best_sellers":
        return await find_cached_shopify_top_selling_products(db, limit=limit, phone=phone)
    if category == "all":
        return await find_cached_shopify_category_products(db, "all", limit=limit, offset=offset, phone=phone)
    if category.startswith("dynamic:"):
        return await find_cached_shopify_category_products(
            db,
            category.removeprefix("dynamic:"),
            limit=limit,
            offset=offset,
            phone=phone,
        )
    return await find_cached_shopify_category_products(db, category, limit=limit, offset=offset, phone=phone)


async def try_send_category_more_button(
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
            localized(language, f"Do you want to see more {label} products?", f"Aur {label} products dekhne hain?"),
            [{"id": f"catalog:more:{category_key}:{next_page}", "title": "Show more"}],
        )
    except Exception:
        return False
    return True


def catalog_page_number(query: str) -> int:
    match = re.search(r"\bcatalog page (\d+)\b", " ".join((query or "").lower().split()))
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


async def try_send_catalog_category_list(
    db: Session,
    phone: str,
    language: str = "english",
    page: int = 1,
) -> bool:
    page = max(1, page)
    dynamic_rows = await find_cached_shopify_catalog_categories(db, limit=50, phone=phone)
    rows = []
    if page == 1:
        rows.extend(CATALOG_CATEGORY_ROWS)

    first_page_dynamic_slots = 9 - len(CATALOG_CATEGORY_ROWS)
    start = 0 if page == 1 else first_page_dynamic_slots + ((page - 2) * CATALOG_PAGE_SIZE)
    end = start + first_page_dynamic_slots if page == 1 else start + CATALOG_PAGE_SIZE
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
            localized(language, "Which category would you like to view?", "Kaunsi category dekhni hai?"),
            "Categories",
            rows,
            "Catalog",
            "Choose category",
        )
    except Exception:
        return False
    return True


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
    products = await find_cached_shopify_cross_sell_products(db, text, base_products, limit=3, phone=phone)
    if not products:
        return
    sent = await try_send_product_carousel(
        phone,
        products,
        localized(reply_language(text), "You may also like these.", "Aapko ye bhi pasand aa sakta hai."),
    )
    if sent:
        save_message(db, phone, "[carousel] Cross-sell products", "outgoing")


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
            save_message(db, phone, f"[image] {caption}", "outgoing")
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
