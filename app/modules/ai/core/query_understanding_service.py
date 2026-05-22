import json
import re
from dataclasses import dataclass, field

import requests

from app.config import settings
from app.modules.ai.core.intelligence_service import detect_query_intent
from app.modules.ai.core.sales_recommendations_service import extract_requested_limit, is_top_selling_request


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 15
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b", re.I)

TOOL_BY_INTENT = {
    "order_status": "get_order_status",
    "tracking_question": "get_order_status",
    "top_selling_products": "search_products",
    "catalog_request": "search_products",
    "image_request": "search_products",
    "price_question": "search_products",
    "policy_question": "get_policy_or_faq",
    "faq_question": "get_policy_or_faq",
    "service_request": "get_services",
    "general": "search_knowledge",
}

COMMON_FIXES = {
    "iamge": "image",
    "imgae": "image",
    "produt": "product",
    "produts": "products",
    "prouct": "product",
    "jayda": "jyada",
    "jada": "jyada",
    "zyada": "jyada",
    "whatatp": "whatsapp",
    "whataap": "whatsapp",
    "phaucha": "pahuncha",
    "pahucha": "pahuncha",
}


@dataclass(frozen=True)
class QueryUnderstanding:
    original_message: str
    normalized_query: str
    intent: str
    entities: dict = field(default_factory=dict)
    confidence: float = 0.5
    tool: str = "search_knowledge"
    source: str = "rules"


def understand_message(message: str) -> QueryUnderstanding:
    llm_result = _llm_understanding(message)
    if llm_result:
        return llm_result
    return _rule_understanding(message)


def _llm_understanding(message: str) -> QueryUnderstanding | None:
    api_key = settings.openrouter_api_key
    if not api_key:
        return None

    try:
        response = requests.post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.app_url,
                "X-Title": settings.app_name,
            },
            json={
                "model": settings.router_model or settings.openrouter_model or "openai/gpt-4o-mini",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Normalize this WhatsApp ecommerce message and extract routing data. "
                            "Fix typos and Hinglish, but keep product names and order IDs intact. "
                            "Return only JSON with keys: normalized_query, intent, entities, "
                            "confidence, tool. Allowed intents: order_status, top_selling_products, "
                            "catalog_request, image_request, price_question, policy_question, "
                            "faq_question, service_request, general. Allowed tools: get_order_status, "
                            "search_products, get_customer_profile, get_policy_or_faq, get_services, "
                            "search_knowledge, general_reply."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                "temperature": 0,
                "max_tokens": 220,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        payload = json.loads(match.group(0) if match else content)
    except Exception:
        return None

    normalized = str(payload.get("normalized_query") or message).strip() or message
    intent = str(payload.get("intent") or "general").strip() or "general"
    entities = payload.get("entities") if isinstance(payload.get("entities"), dict) else {}
    confidence = _clamp_confidence(payload.get("confidence"), 0.6)
    tool = str(payload.get("tool") or TOOL_BY_INTENT.get(intent, "search_knowledge")).strip()
    return QueryUnderstanding(
        original_message=message,
        normalized_query=normalized,
        intent=intent,
        entities=_merge_rule_entities(normalized, entities),
        confidence=confidence,
        tool=tool if tool in set(TOOL_BY_INTENT.values()) | {"get_customer_profile", "general_reply"} else "search_knowledge",
        source="llm",
    )


def _rule_understanding(message: str) -> QueryUnderstanding:
    normalized = _normalize_text(message)
    query_intent = detect_query_intent(normalized)
    intent = query_intent.name
    if is_top_selling_request(normalized):
        intent = "top_selling_products"
    elif query_intent.name == "tracking_question":
        intent = "order_status"

    confidence = min(0.95, 0.35 + (query_intent.score * 0.15))
    if intent in {"top_selling_products", "order_status"}:
        confidence = max(confidence, 0.75)

    return QueryUnderstanding(
        original_message=message,
        normalized_query=normalized,
        intent=intent,
        entities=_merge_rule_entities(normalized, {}),
        confidence=confidence,
        tool=TOOL_BY_INTENT.get(intent, "search_knowledge"),
        source="rules",
    )


def _normalize_text(message: str) -> str:
    tokens = []
    for token in re.findall(r"\S+", message or ""):
        key = re.sub(r"[^a-zA-Z0-9]", "", token).lower()
        replacement = COMMON_FIXES.get(key)
        tokens.append(replacement if replacement else token)
    return " ".join(tokens).strip()


def _merge_rule_entities(message: str, entities: dict) -> dict:
    merged = dict(entities)
    order_match = ORDER_RE.search(message or "")
    if order_match and not merged.get("order_id"):
        merged["order_id"] = next((group.upper() for group in order_match.groups() if group), None)
    requested_limit = extract_requested_limit(message, default=0)
    if requested_limit and not merged.get("limit"):
        merged["limit"] = requested_limit
    return {key: value for key, value in merged.items() if value not in (None, "", [])}


def _clamp_confidence(value, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(confidence, 1.0))
