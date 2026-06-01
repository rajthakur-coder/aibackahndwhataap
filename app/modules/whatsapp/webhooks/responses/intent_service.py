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

__all__ = [
    "requested_limit_from_understanding",
    "understanding_context",
    "looks_like_catalog_request",
    "is_catalog_page_request",
    "looks_like_image_request",
    "request_terms",
    "is_main_menu_request",
    "squash_repeated_letters",
    "reply_language",
    "localized",
]
