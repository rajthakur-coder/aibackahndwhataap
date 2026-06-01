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


def extract_budget(query: str) -> float | None:
    match = BUDGET_RE.search(query or "")
    if not match:
        return None
    return _price_number(match.group(1))

def _ecommerce_candidates(db: Session, tenant_id: str | None = None) -> list[dict]:
    query = select(EcommerceProduct).order_by(EcommerceProduct.updated_at.desc()).limit(300)
    if tenant_id:
        query = (
            select(EcommerceProduct)
            .where(EcommerceProduct.tenant_id == tenant_id)
            .order_by(EcommerceProduct.updated_at.desc())
            .limit(300)
        )
    rows = db.execute(
        query
    ).scalars().all()
    candidates = []
    for row in rows:
        image_urls = _json_list(row.image_urls)
        candidates.append(
            {
                "source": "ecommerce",
                "tenant_id": row.tenant_id,
                "platform": row.platform,
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
                "sku": row.sku,
                "external_id": row.external_id,
                "shopify_product_id": row.shopify_product_id,
                "retailer_id": _first_retailer_id(row),
            }
        )
    return candidates

def _product_sales_lookup(
    products: list[EcommerceProduct],
    product_id_totals: dict[str, int],
    sku_totals: dict[str, int],
    name_totals: dict[str, int],
) -> dict[tuple[str, str], EcommerceProduct]:
    lookup = {}
    for product in products:
        for product_id in {product.external_id, product.shopify_product_id}:
            if product_id and str(product_id) in product_id_totals:
                lookup[("product_id", str(product_id))] = product
        for sku in _product_skus(product):
            if sku in sku_totals:
                lookup[("sku", sku)] = product
        title_key = (product.title or "").lower()
        if title_key in name_totals:
            lookup[("name", title_key)] = product
    return lookup

def _product_dict(product: EcommerceProduct) -> dict:
    image_urls = _json_list(product.image_urls)
    return {
        "source": "ecommerce",
        "tenant_id": product.tenant_id,
        "platform": product.platform,
        "title": product.title,
        "description": product.description,
        "category": product.product_type,
        "brand": product.vendor,
        "tags": product.tags,
        "product_type": product.product_type,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "price": _price_range({"price_min": product.price_min, "price_max": product.price_max}),
        "product_url": product.product_url,
        "image_url": image_urls[0] if image_urls else None,
        "sku": product.sku,
        "external_id": product.external_id,
        "shopify_product_id": product.shopify_product_id,
        "retailer_id": _first_retailer_id(product),
    }

def _product_skus(product: EcommerceProduct) -> set[str]:
    skus = set()
    if product.sku:
        skus.update(part.strip() for part in product.sku.split(",") if part.strip())
    skus.update(_json_list(product.skus))
    return skus

def _cross_sell_terms(query: str, base_products: list[dict]) -> set[str]:
    haystack = " ".join(
        [query or ""]
        + [
            " ".join(
                str(product.get(key) or "")
                for key in ("title", "description", "category", "tags", "product_type")
            )
            for product in base_products
        ]
    ).lower()
    tokens = set(_tokens(haystack))
    terms = set()
    for token in tokens:
        terms.update(CROSS_SELL_MAP.get(token, set()))
    return terms

def _co_purchase_terms(db: Session, base_products: list[dict], tenant_id: str | None = None) -> set[str]:
    base_names = {(product.get("title") or "").lower() for product in base_products}
    base_skus = {
        str(product.get("sku") or "").lower()
        for product in base_products
        if product.get("sku")
    }
    base_ids = {
        str(product.get(key) or "").lower()
        for product in base_products
        for key in ("external_id", "shopify_product_id", "retailer_id")
        if product.get(key)
    }
    if not base_names and not base_skus and not base_ids:
        return set()

    counts: Counter[str] = Counter()
    query = select(EcommerceOrder).order_by(EcommerceOrder.updated_at.desc()).limit(1000)
    if tenant_id:
        query = (
            select(EcommerceOrder)
            .where(EcommerceOrder.tenant_id == tenant_id)
            .order_by(EcommerceOrder.updated_at.desc())
            .limit(1000)
        )
    orders = db.execute(query).scalars().all()
    for order in orders:
        items = _json_dict_list(order.items)
        if not items:
            continue
        has_base = any(_item_matches_product(item, base_names, base_skus, base_ids) for item in items)
        if not has_base:
            continue
        for item in items:
            name = str(item.get("name") or "").strip()
            if not name or _item_matches_product(item, base_names, base_skus, base_ids):
                continue
            counts[name.lower()] += _quantity_number(item.get("quantity"))

    return {name for name, _count in counts.most_common(5)}

def _item_matches_product(item: dict, names: set[str], skus: set[str], product_ids: set[str]) -> bool:
    item_name = str(item.get("name") or "").lower()
    item_sku = str(item.get("sku") or "").lower()
    item_product_id = str(item.get("product_id") or "").lower()
    return item_name in names or item_sku in skus or item_product_id in product_ids

def _first_retailer_id(product: EcommerceProduct) -> str | None:
    skus = _product_skus(product)
    if skus:
        return sorted(skus)[0]
    return product.external_id or product.shopify_product_id

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

def _quantity_number(value: str | float | int | None) -> int:
    try:
        quantity = int(float(value or 1))
    except (TypeError, ValueError):
        return 1
    return max(1, quantity)

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

def _json_dict_list(value: str | None) -> list[dict]:
    if not value:
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [item for item in loaded if isinstance(item, dict)]

__all__ = [
    "extract_budget",
    "_ecommerce_candidates",
    "_product_sales_lookup",
    "_product_dict",
    "_product_skus",
    "_cross_sell_terms",
    "_co_purchase_terms",
    "_item_matches_product",
    "_first_retailer_id",
    "_tokens",
    "_score",
    "_price_number",
    "_quantity_number",
    "_price_range",
    "_json_list",
    "_json_dict_list",
]
