import json
import re
from dataclasses import dataclass, field

from app.modules.ai.intelligence.intelligence_service import detect_query_intent
from app.modules.ai.recommendations.sales_recommendations_service import extract_requested_limit, is_top_selling_request
from app.modules.headless.llm_provider import chat_completion


ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b", re.I)
BARE_ORDER_RE = re.compile(r"^\s*#?([A-Za-z0-9][A-Za-z0-9-]{2,})\s*$")
FAST_RULE_INTENTS = {
    "menu_request",
    "order_status",
    "top_selling_products",
    "catalog_request",
    "image_request",
    "price_question",
    "contact_request",
    "policy_question",
    "faq_question",
    "out_of_scope",
}

TOOL_BY_INTENT = {
    "greeting": "general_reply",
    "menu_request": "general_reply",
    "order_status": "get_order_status",
    "tracking_question": "get_order_status",
    "top_selling_products": "search_products",
    "catalog_request": "search_products",
    "image_request": "search_products",
    "price_question": "search_products",
    "contact_request": "get_policy_or_faq",
    "policy_question": "get_policy_or_faq",
    "faq_question": "get_policy_or_faq",
    "service_request": "get_services",
    "general": "search_knowledge",
    "out_of_scope": "out_of_scope",
}

OUT_OF_SCOPE_CODING_TERMS = {
    "api",
    "app",
    "code",
    "coding",
    "css",
    "html",
    "java",
    "javascript",
    "js",
    "next",
    "nextjs",
    "node",
    "nodejs",
    "program",
    "programming",
    "python",
    "react",
    "script",
    "typescript",
}

OUT_OF_SCOPE_LEARNING_TERMS = {
    "build",
    "create",
    "define",
    "explain",
    "learn",
    "make",
    "meaning",
    "setup",
    "teach",
    "tutorial",
    "what",
}

LOW_INFORMATION_TERMS = {
    "bro",
    "checking",
    "hmm",
    "hmmm",
    "nice",
    "ok",
    "okay",
    "test",
    "testing",
    "thank",
    "thanks",
    "yo",
    "yoo",
}

CONTACT_TERMS = {
    "call",
    "contact",
    "email",
    "mail",
    "mobile",
    "number",
    "phone",
    "support",
    "whatsapp",
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
    rule_result = _rule_understanding(message)
    if _should_use_rule_fast_path(rule_result):
        return rule_result

    llm_result = _llm_understanding(message)
    if llm_result:
        return llm_result
    return rule_result


def _llm_understanding(message: str) -> QueryUnderstanding | None:
    try:
        response = chat_completion(
            None,
            tenant_id="default",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Normalize this WhatsApp ecommerce message and extract routing data. "
                        "Fix typos and Hinglish, but keep product names and order IDs intact. "
                        "Return only JSON with keys: normalized_query, intent, entities, "
                        "confidence, tool. Allowed intents: greeting, menu_request, order_status, top_selling_products, "
                        "catalog_request, image_request, price_question, contact_request, policy_question, "
                        "faq_question, service_request, general. Allowed tools: get_order_status, "
                        "search_products, get_customer_profile, get_policy_or_faq, get_services, "
                        "search_knowledge, general_reply. For product searches, entities may include "
                        "product_type, category, budget_max, color, size, material, use_case, "
                        "attributes as a list, brand, and limit."
                    ),
                },
                {"role": "user", "content": message},
            ],
            purpose="understanding",
            temperature=0,
            max_tokens=220,
        )
        content = response.content
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
    if _looks_out_of_scope(normalized):
        return QueryUnderstanding(
            original_message=message,
            normalized_query=normalized,
            intent="out_of_scope",
            entities={},
            confidence=0.95,
            tool="out_of_scope",
            source="rules",
        )

    query_intent = detect_query_intent(normalized)
    intent = query_intent.name
    if _looks_like_contact_request(normalized):
        intent = "contact_request"
    elif _looks_like_greeting_or_menu(normalized):
        intent = "menu_request"
    elif _bare_order_id(normalized):
        intent = "order_status"
    elif is_top_selling_request(normalized):
        intent = "top_selling_products"
    elif query_intent.name == "tracking_question":
        intent = "order_status"

    confidence = min(0.95, 0.35 + (query_intent.score * 0.15))
    if intent in {"menu_request", "top_selling_products", "order_status"}:
        confidence = max(confidence, 0.75)
    elif intent in FAST_RULE_INTENTS and query_intent.score >= 2:
        confidence = max(confidence, 0.65)
    elif intent in {"catalog_request", "image_request", "price_question", "contact_request", "policy_question"} and query_intent.score >= 1:
        confidence = max(confidence, 0.55)

    if intent == "contact_request":
        confidence = 0.95

    return QueryUnderstanding(
        original_message=message,
        normalized_query=normalized,
        intent=intent,
        entities=_merge_rule_entities(normalized, {}),
        confidence=confidence,
        tool=TOOL_BY_INTENT.get(intent, "search_knowledge"),
        source="rules",
    )


def _should_use_rule_fast_path(result: QueryUnderstanding) -> bool:
    if result.intent in {"menu_request", "order_status", "top_selling_products"}:
        return True
    return result.intent in FAST_RULE_INTENTS and result.confidence >= 0.55


def _normalize_text(message: str) -> str:
    tokens = []
    for token in re.findall(r"\S+", message or ""):
        key = re.sub(r"[^a-zA-Z0-9]", "", token).lower()
        replacement = COMMON_FIXES.get(key)
        tokens.append(replacement if replacement else token)
    return " ".join(tokens).strip()


def _looks_like_greeting_or_menu(message: str) -> bool:
    tokens = [re.sub(r"(.)\1{2,}", r"\1\1", token.lower()) for token in re.findall(r"[a-zA-Z0-9]+", message or "")]
    if not tokens:
        return False
    if any(token in {"menu", "help", "start"} for token in tokens[:4]):
        return True
    if len(tokens) <= 3 and set(tokens) <= LOW_INFORMATION_TERMS:
        return True
    if tokens[0] in {"hi", "hii", "hello", "hey", "namaste"} and len(tokens) <= 4:
        intent_words = {"order", "track", "product", "products", "catalog", "price", "image", "status"}
        return not bool(set(tokens[1:]) & intent_words)
    return False


def _looks_like_contact_request(message: str) -> bool:
    tokens = {
        re.sub(r"[^a-zA-Z0-9]", "", token).lower()
        for token in re.findall(r"[a-zA-Z0-9]+", message or "")
    }
    tokens.discard("")
    lowered = (message or "").lower()
    if not tokens & CONTACT_TERMS:
        return False
    request_terms = {
        "bata",
        "batao",
        "chahiye",
        "chaiye",
        "de",
        "do",
        "give",
        "kya",
        "share",
        "send",
    }
    return bool(tokens & request_terms) or any(
        phrase in lowered
        for phrase in (
            "contact us",
            "customer care",
            "support team",
            "support number",
            "email id",
            "mail id",
            "phone number",
            "mobile number",
            "whatsapp number",
        )
    )


def _looks_out_of_scope(message: str) -> bool:
    tokens = {
        re.sub(r"[^a-zA-Z0-9]", "", token).lower()
        for token in re.findall(r"[a-zA-Z0-9.+#-]+", message or "")
    }
    tokens.discard("")
    lowered = (message or "").lower()

    if not tokens & OUT_OF_SCOPE_CODING_TERMS:
        return False

    if tokens & OUT_OF_SCOPE_LEARNING_TERMS:
        return True

    coding_phrases = (
        "what is",
        "how to",
        "how do i",
        "how can i",
        "new next",
        "next js",
        "next.js",
        "create a new",
    )
    return any(phrase in lowered for phrase in coding_phrases)


def _merge_rule_entities(message: str, entities: dict) -> dict:
    merged = dict(entities)
    order_match = ORDER_RE.search(message or "")
    if order_match and not merged.get("order_id"):
        merged["order_id"] = next((group.upper() for group in order_match.groups() if group), None)
    elif not merged.get("order_id"):
        bare_order_id = _bare_order_id(message)
        if bare_order_id:
            merged["order_id"] = bare_order_id
    requested_limit = extract_requested_limit(message, default=0)
    if requested_limit and not merged.get("limit"):
        merged["limit"] = requested_limit
    return {key: value for key, value in merged.items() if value not in (None, "", [])}


def _bare_order_id(message: str) -> str | None:
    value = str(message or "").strip()
    match = BARE_ORDER_RE.match(value)
    if not match:
        return None
    order_id = match.group(1)
    if not value.startswith("#") and not any(char.isdigit() for char in order_id):
        return None
    return order_id.upper()


def _clamp_confidence(value, default: float) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(confidence, 1.0))
