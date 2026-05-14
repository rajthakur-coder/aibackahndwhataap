import json
import re
from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceOrder, EcommerceProduct
from app.models.entities import StructuredProduct
from app.services.intelligence import detect_query_intent
from app.services.product_search import score_search_text, search_terms


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


def find_top_selling_products(db: Session, limit: int = 2) -> list[dict]:
    orders = db.query(EcommerceOrder).order_by(EcommerceOrder.updated_at.desc()).limit(1000).all()
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

    products = db.query(EcommerceProduct).limit(1000).all()
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
) -> list[dict]:
    exclude_titles = {(product.get("title") or "").lower() for product in base_products}
    exclude_ids = {
        str(product.get(key) or "")
        for product in base_products
        for key in ("external_id", "shopify_product_id", "sku", "retailer_id")
        if product.get(key)
    }

    terms = _cross_sell_terms(query, base_products)
    co_purchase_names = _co_purchase_terms(db, base_products)
    terms.update(co_purchase_names)
    if not terms:
        return []

    query_terms = search_terms(" ".join(sorted(terms)))
    scored = []
    for product in _ecommerce_candidates(db):
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


def find_product_recommendations(db: Session, query: str, limit: int = 5) -> list[dict]:
    if not is_sales_recommendation_request(query):
        return []

    budget = extract_budget(query)
    query_terms = search_terms(query)
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
                "sku": row.sku,
                "external_id": row.external_id,
                "shopify_product_id": row.shopify_product_id,
                "retailer_id": _first_retailer_id(row),
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


def _co_purchase_terms(db: Session, base_products: list[dict]) -> set[str]:
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
    orders = db.query(EcommerceOrder).order_by(EcommerceOrder.updated_at.desc()).limit(1000).all()
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
