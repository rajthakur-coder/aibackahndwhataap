import re
from dataclasses import dataclass


TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")

INTENT_TERMS = {
    "catalog_request": {
        "catalog",
        "catalogue",
        "chahiye",
        "chaiye",
        "collection",
        "collections",
        "footwear",
        "joota",
        "joote",
        "juta",
        "jute",
        "kapda",
        "kapde",
        "menu",
        "mobile",
        "phone",
        "product",
        "products",
        "service",
        "services",
        "shoe",
        "shoes",
        "show",
        "dikha",
        "dika",
        "dikhao",
        "tshirt",
    },
    "image_request": {
        "image",
        "images",
        "photo",
        "photos",
        "pic",
        "picture",
        "tasveer",
        "dikha",
        "dika",
        "dikhao",
        "bhejo",
    },
    "price_question": {
        "cost",
        "fees",
        "kitna",
        "package",
        "price",
        "pricing",
        "rate",
        "rs",
        "₹",
    },
    "policy_question": {
        "cancel",
        "cancellation",
        "exchange",
        "policy",
        "refund",
        "return",
        "shipping",
        "terms",
        "warranty",
    },
    "tracking_question": {
        "delivery",
        "order",
        "shipment",
        "status",
        "track",
        "tracking",
    },
    "faq_question": {
        "can",
        "faq",
        "help",
        "how",
        "kya",
        "what",
        "when",
        "where",
        "why",
    },
}

POLICY_TYPE_TERMS = {
    "return": {"return", "exchange", "refund"},
    "shipping": {"shipping", "delivery", "shipment"},
    "cancellation": {"cancel", "cancellation"},
    "privacy": {"privacy"},
    "terms": {"terms", "condition", "conditions"},
}


@dataclass(frozen=True)
class QueryIntent:
    name: str
    score: int
    policy_type: str | None = None


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text or "")}


def detect_query_intent(message: str) -> QueryIntent:
    tokens = _tokens(message)
    lowered = (message or "").lower()
    best_name = "general"
    best_score = 0

    for intent, terms in INTENT_TERMS.items():
        score = 0
        for term in terms:
            if term in tokens:
                score += 1
            elif term in lowered:
                score += 1
        if score > best_score:
            best_name = intent
            best_score = score

    policy_type = None
    if best_name == "policy_question":
        policy_type = detect_policy_type(message)

    return QueryIntent(name=best_name, score=best_score, policy_type=policy_type)


def detect_policy_type(message: str) -> str | None:
    tokens = _tokens(message)
    for policy_type, terms in POLICY_TYPE_TERMS.items():
        if tokens & terms:
            return policy_type
    return None
