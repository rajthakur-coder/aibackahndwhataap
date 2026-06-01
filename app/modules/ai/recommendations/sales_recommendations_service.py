import json
import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceOrder, EcommerceProduct
from app.modules.ai.intelligence.intelligence_service import detect_query_intent
from app.modules.ai.search.product_search_service import score_search_text, search_terms


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
REQUESTED_LIMIT_RE = re.compile(r"\b([1-9]|10)\b")
BUDGET_RE = re.compile(
    r"(?:under|below|less than|upto|up to|budget|andar|neeche|kam|<=?)\s*(?:rs\.?|inr|₹)?\s*([\d,]+)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"[\d,]+(?:\.\d{1,2})?")
SALES_TERMS = {
    "best",
    "chahiye",
    "chaiye",
    "recommend",
    "suggest",
    "dikhao",
    "dika",
    "show",
    "under",
    "below",
    "budget",
    "price",
    "shoes",
    "shoe",
    "joota",
    "joote",
    "juta",
    "jute",
    "kapda",
    "kapde",
    "mobile",
    "phone",
    "tshirt",
    "product",
    "products",
}
TOP_SELLING_PHRASES = {
    "best selling",
    "bestselling",
    "highest sale",
    "most sale",
    "most sold",
    "sabse jada sale",
    "sabse jayda sale",
    "sabse jyada sale",
    "sabse zyada sale",
    "top sale",
    "top selling",
}
CROSS_SELL_MAP = {
    "shoe": {"sock", "socks", "cleaner", "insole", "insoles", "lace", "laces"},
    "shoes": {"sock", "socks", "cleaner", "insole", "insoles", "lace", "laces"},
    "sneaker": {"sock", "socks", "cleaner", "insole", "insoles", "lace", "laces"},
    "sneakers": {"sock", "socks", "cleaner", "insole", "insoles", "lace", "laces"},
    "mobile": {"cover", "case", "charger", "cable", "power", "bank", "earphone"},
    "phone": {"cover", "case", "charger", "cable", "power", "bank", "earphone"},
    "power": {"cable", "charger", "adapter"},
    "bank": {"cable", "charger", "adapter"},
    "screwdriver": {"tool", "pliers", "tape", "tester", "drill", "bits"},
    "tshirt": {"jeans", "jacket", "shirt", "shorts"},
    "shirt": {"jeans", "jacket", "tshirt", "shorts"},
}


from app.modules.ai.recommendations.sales_recommendation_helpers import *

def is_sales_recommendation_request(query: str) -> bool:
    intent = detect_query_intent(query)
    terms = set(_tokens(query))
    return intent.name in {"catalog_request", "price_question"} or bool(terms & SALES_TERMS)


def is_top_selling_request(query: str) -> bool:
    lowered = (query or "").lower()
    return any(phrase in lowered for phrase in TOP_SELLING_PHRASES)


def extract_requested_limit(query: str, default: int = 3) -> int:
    lowered = (query or "").lower()
    word_limits = {
        "one": 1,
        "ek": 1,
        "do": 2,
        "two": 2,
        "teen": 3,
        "three": 3,
        "char": 4,
        "four": 4,
        "panch": 5,
        "five": 5,
    }
    for word, limit in word_limits.items():
        if re.search(rf"\b{word}\b", lowered):
            return limit

    match = REQUESTED_LIMIT_RE.search(lowered)
    if match:
        return int(match.group(1))

    return default


def find_top_selling_products(db: Session, limit: int = 2, tenant_id: str | None = None) -> list[dict]:
    query = select(EcommerceOrder).order_by(EcommerceOrder.updated_at.desc()).limit(1000)
    if tenant_id:
        query = (
            select(EcommerceOrder)
            .where(EcommerceOrder.tenant_id == tenant_id)
            .order_by(EcommerceOrder.updated_at.desc())
            .limit(1000)
        )
    orders = db.execute(query).scalars().all()
    sales_by_key: dict[tuple[str, str], dict] = {}
    product_id_totals: defaultdict[str, int] = defaultdict(int)
    sku_totals: defaultdict[str, int] = defaultdict(int)
    name_totals: defaultdict[str, int] = defaultdict(int)

    for order in orders:
        for item in _json_dict_list(order.items):
            quantity = _quantity_number(item.get("quantity"))
            name = str(item.get("name") or "").strip()
            sku = str(item.get("sku") or "").strip()
            product_id = str(item.get("product_id") or "").strip()

            if product_id:
                product_id_totals[product_id] += quantity
                key = ("product_id", product_id)
            elif sku:
                sku_totals[sku] += quantity
                key = ("sku", sku)
            elif name:
                name_totals[name.lower()] += quantity
                key = ("name", name.lower())
            else:
                continue

            if key not in sales_by_key:
                sales_by_key[key] = {
                    "title": name or sku or product_id,
                    "sku": sku,
                    "external_id": product_id,
                    "sales_count": 0,
                }
            sales_by_key[key]["sales_count"] += quantity

    if not sales_by_key:
        return []

    product_query = select(EcommerceProduct).limit(1000)
    if tenant_id:
        product_query = select(EcommerceProduct).where(EcommerceProduct.tenant_id == tenant_id).limit(1000)
    products = db.execute(product_query).scalars().all()
    product_lookup = _product_sales_lookup(products, product_id_totals, sku_totals, name_totals)
    ranked = sorted(sales_by_key.values(), key=lambda item: item["sales_count"], reverse=True)
    results = []
    seen_titles = set()

    for sold_item in ranked:
        product = product_lookup.get(("product_id", str(sold_item.get("external_id") or "")))
        if not product and sold_item.get("sku"):
            product = product_lookup.get(("sku", sold_item["sku"]))
        if not product:
            product = product_lookup.get(("name", sold_item["title"].lower()))

        result = _product_dict(product) if product else dict(sold_item)
        result["sales_count"] = sold_item["sales_count"]
        title_key = (result.get("title") or "").lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        results.append(result)
        if len(results) >= max(1, min(limit, 10)):
            break

    return results


def find_cross_sell_products(
    db: Session,
    query: str,
    base_products: list[dict],
    limit: int = 3,
    tenant_id: str | None = None,
) -> list[dict]:
    exclude_titles = {(product.get("title") or "").lower() for product in base_products}
    exclude_ids = {
        str(product.get(key) or "")
        for product in base_products
        for key in ("external_id", "shopify_product_id", "sku", "retailer_id")
        if product.get(key)
    }

    terms = _cross_sell_terms(query, base_products)
    co_purchase_names = _co_purchase_terms(db, base_products, tenant_id=tenant_id)
    terms.update(co_purchase_names)
    if not terms:
        return []

    query_terms = search_terms(" ".join(sorted(terms)))
    scored = []
    for product in _ecommerce_candidates(db, tenant_id=tenant_id):
        title_key = (product.get("title") or "").lower()
        if title_key in exclude_titles:
            continue
        product_ids = {
            str(product.get(key) or "")
            for key in ("external_id", "shopify_product_id", "sku", "retailer_id")
            if product.get(key)
        }
        if product_ids & exclude_ids:
            continue

        searchable = " ".join(
            str(product.get(key) or "")
            for key in ("title", "description", "category", "brand", "tags", "product_type")
        )
        score = score_search_text(query_terms, searchable)
        if title_key in co_purchase_names:
            score += 4.0
        if product.get("image_url"):
            score += 0.15
        if product.get("product_url"):
            score += 0.1
        if score > 0:
            scored.append((score, product))

    ranked = [product for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)]
    return ranked[: max(1, min(limit, 10))]


def find_product_recommendations(db: Session, query: str, limit: int = 5, tenant_id: str | None = None) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []

    budget = extract_budget(query)
    query_terms = search_terms(query)
    candidates = _ecommerce_candidates(db, tenant_id=tenant_id)
    scored = []

    for product in candidates:
        price_value = _price_number(product.get("price") or product.get("price_min") or "")
        if budget and price_value and price_value > budget:
            continue

        searchable = " ".join(
            str(product.get(key) or "")
            for key in ("title", "description", "category", "brand", "tags", "product_type")
        )
        score = score_search_text(query_terms, searchable)
        if budget and price_value:
            score += max(0.0, 1.0 - (price_value / budget))
        if product.get("image_url"):
            score += 0.15
        if product.get("product_url"):
            score += 0.1
        scored.append((score, product))

    if not scored:
        return []

    ranked = [product for score, product in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0]
    if not ranked:
        ranked = [product for _score, product in sorted(scored, key=lambda item: item[0], reverse=True)]
    return ranked[: max(1, min(limit, 10))]


def recommendation_intro(query: str, products: list[dict]) -> str:
    budget = extract_budget(query)
    if is_top_selling_request(query) or any(product.get("sales_count") for product in products):
        heading = "Top selling products:"
    elif budget:
        heading = f"Best options under {int(budget)}:"
    else:
        heading = "Recommended options:"

    lines = [heading]
    for index, product in enumerate(products, start=1):
        line = f"{index}. {product['title']}"
        price = product.get("price") or _price_range(product)
        if price:
            line += f" - {price}"
        if product.get("brand"):
            line += f" ({product['brand']})"
        if product.get("sales_count"):
            line += f" - Sold: {product['sales_count']}"
        if product.get("product_url"):
            line += f"\n{product['product_url']}"
        lines.append(line)
    return "\n\n".join(lines)

def recommendation_caption(product: dict) -> str:
    parts = [product["title"]]
    price = product.get("price") or _price_range(product)
    if price:
        parts.append(f"Price: {price}")
    if product.get("brand"):
        parts.append(f"Brand: {product['brand']}")
    if product.get("sales_count"):
        parts.append(f"Sold: {product['sales_count']}")
    if product.get("product_url"):
        parts.append(product["product_url"])
    return "\n".join(parts)
