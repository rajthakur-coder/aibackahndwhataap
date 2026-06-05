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
    find_cached_default_catalog_categories,
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
        return await find_cached_top_selling_products(db, limit=limit, phone=phone)
    if category == "all":
        return await find_cached_category_products(db, "all", limit=limit, offset=offset, phone=phone)
    if category.startswith("dynamic:"):
        return await find_cached_category_products(
            db,
            category.removeprefix("dynamic:"),
            limit=limit,
            offset=offset,
            phone=phone,
        )
    return await find_cached_category_products(db, category, limit=limit, offset=offset, phone=phone)

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
    timing=None,
) -> bool:
    page = max(1, page)
    if timing:
        with timing.stage("category_cache"):
            dynamic_rows = await find_cached_catalog_categories(db, limit=50, phone=phone)
    else:
        dynamic_rows = await find_cached_catalog_categories(db, limit=50, phone=phone)
    default_rows = await find_cached_default_catalog_categories(db, phone=phone)

    if default_rows is None:
        rows = []
        if page == 1:
            rows.extend(CATALOG_CATEGORY_ROWS)

        first_page_dynamic_slots = 9 - len(CATALOG_CATEGORY_ROWS)
        start = 0 if page == 1 else first_page_dynamic_slots + ((page - 2) * CATALOG_PAGE_SIZE)
        end = start + first_page_dynamic_slots if page == 1 else start + CATALOG_PAGE_SIZE
        rows.extend(dynamic_rows[start:end])
        has_more_rows = end < len(dynamic_rows)
    else:
        ordered_rows = sorted(
            [*default_rows, *dynamic_rows],
            key=lambda item: (int(item.get("sort_order") or 0), str(item.get("title") or "").lower()),
        )
        start = (page - 1) * 9
        end = start + 9
        rows = ordered_rows[start:end]
        has_more_rows = end < len(ordered_rows)

    if has_more_rows and len(rows) < 10:
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
    rows = [_category_row_without_count(row) for row in rows]
    try:
        if timing:
            with timing.stage("whatsapp_send_list"):
                await run_in_threadpool(
                    send_whatsapp_list,
                    phone,
                    localized(language, "Which category would you like to view?", "Kaunsi category dekhni hai?"),
                    "Categories",
                    rows,
                    "Catalog",
                    "Choose category",
                )
        else:
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

def _category_row_without_count(row: dict) -> dict:
    row_id = str(row.get("id") or "")
    if row_id.startswith("catalog:page:"):
        return row
    return {**row, "description": ""}

__all__ = [
    "selected_catalog_category",
    "selected_catalog_product_page",
    "products_for_catalog_category",
    "try_send_category_more_button",
    "catalog_page_number",
    "try_send_catalog_category_list",
]
