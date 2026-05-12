import json
import re
from collections import Counter

from sqlalchemy.orm import Session

from app.models.entities import EcommerceProduct, StructuredProduct
from app.services.intelligence import detect_query_intent


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
BUDGET_RE = re.compile(
    r"(?:under|below|less than|upto|up to|budget|andar|neeche|kam|<=?)\s*(?:rs\.?|inr|₹)?\s*([\d,]+)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"[\d,]+(?:\.\d{1,2})?")
SALES_TERMS = {
    "best",
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
    "product",
    "products",
}


def is_sales_recommendation_request(query: str) -> bool:
    intent = detect_query_intent(query)
    terms = set(_tokens(query))
    return intent.name in {"catalog_request", "price_question"} or bool(terms & SALES_TERMS)


def find_product_recommendations(db: Session, query: str, limit: int = 5) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []

    budget = extract_budget(query)
    query_terms = Counter(token for token in _tokens(query) if token not in SALES_TERMS)
    candidates = _ecommerce_candidates(db) + _structured_candidates(db)
    scored = []

    for product in candidates:
        price_value = _price_number(product.get("price") or product.get("price_min") or "")
        if budget and price_value and price_value > budget:
            continue

        searchable = " ".join(
            str(product.get(key) or "")
            for key in ("title", "description", "category", "brand", "tags", "product_type")
        )
        score = _score(query_terms, searchable)
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
    if budget:
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
    if product.get("product_url"):
        parts.append(product["product_url"])
    return "\n".join(parts)


def extract_budget(query: str) -> float | None:
    match = BUDGET_RE.search(query or "")
    if not match:
        return None
    return _price_number(match.group(1))


def _ecommerce_candidates(db: Session) -> list[dict]:
    rows = db.query(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(300).all()
    candidates = []
    for row in rows:
        image_urls = _json_list(row.image_urls)
        candidates.append(
            {
                "source": "ecommerce",
                "title": row.title,
                "description": row.description,
                "category": row.product_type,
                "brand": row.vendor,
                "tags": row.tags,
                "product_type": row.product_type,
                "price_min": row.price_min,
                "price_max": row.price_max,
                "price": _price_range({"price_min": row.price_min, "price_max": row.price_max}),
                "product_url": row.product_url,
                "image_url": image_urls[0] if image_urls else None,
            }
        )
    return candidates


def _structured_candidates(db: Session) -> list[dict]:
    rows = db.query(StructuredProduct).order_by(StructuredProduct.created_at.desc()).limit(300).all()
    candidates = []
    for row in rows:
        image_urls = _json_list(row.image_urls)
        candidates.append(
            {
                "source": "structured",
                "title": row.title,
                "description": row.description,
                "category": row.category,
                "brand": row.brand,
                "price": row.price,
                "product_url": row.source_url,
                "image_url": image_urls[0] if image_urls else None,
            }
        )
    return candidates


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text or "") if len(token) > 1]


def _score(query_terms: Counter, text: str) -> float:
    text_terms = Counter(_tokens(text))
    if not query_terms:
        return 0.2
    score = 0.0
    for term, count in query_terms.items():
        if text_terms.get(term):
            score += text_terms[term] * count
    return score


def _price_number(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    match = PRICE_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _price_range(product: dict) -> str:
    price_min = product.get("price_min") or ""
    price_max = product.get("price_max") or ""
    if price_min and price_max and price_min != price_max:
        return f"{price_min} - {price_max}"
    return price_min or price_max or ""


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in loaded if isinstance(item, str)]
