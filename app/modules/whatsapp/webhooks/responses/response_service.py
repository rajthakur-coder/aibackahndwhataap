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


from app.modules.whatsapp.webhooks.responses.catalog_service import *
from app.modules.whatsapp.webhooks.responses.intent_service import *
from app.modules.whatsapp.webhooks.responses.menu_service import *
from app.modules.whatsapp.webhooks.responses.product_service import *
