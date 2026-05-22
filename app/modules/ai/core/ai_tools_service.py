import json
import re
from dataclasses import dataclass

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.ai.core.intelligence_service import detect_query_intent


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 20
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b", re.I)
TOOL_NAMES = {
    "get_order_status",
    "search_products",
    "get_customer_profile",
    "get_policy_or_faq",
    "get_services",
    "search_knowledge",
    "general_reply",
}


@dataclass(frozen=True)
class ToolDecision:
    name: str
    reason: str = ""


def decide_tool_for_message(message: str) -> ToolDecision:
    llm_decision = _llm_tool_decision(message)
    if llm_decision:
        return llm_decision

    intent = detect_query_intent(message)
    if intent.name == "tracking_question":
        return ToolDecision("get_order_status", "tracking/order intent")
    if intent.name in {"catalog_request", "price_question", "image_request"}:
        return ToolDecision("search_products", "product/catalog intent")
    if intent.name in {"policy_question", "faq_question"}:
        return ToolDecision("get_policy_or_faq", "policy/faq intent")
    return ToolDecision("search_knowledge", "fallback database search")


def run_ai_tool(db: Session, phone: str, message: str, decision: ToolDecision | None = None) -> dict:
    decision = decision or decide_tool_for_message(message)
    handlers = {
        "get_order_status": _order_status_context,
        "search_products": _product_context,
        "get_customer_profile": _customer_context,
        "get_policy_or_faq": _policy_faq_context,
        "get_services": _service_context,
        "search_knowledge": _database_hint_context,
        "general_reply": _general_context,
    }
    handler = handlers.get(decision.name, _database_hint_context)
    result = handler(db, phone, message)
    return {
        "tool": decision.name,
        "reason": decision.reason,
        "context": result.get("context", ""),
        "data": result.get("data", []),
    }


def _llm_tool_decision(message: str) -> ToolDecision | None:
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
                            "Choose one tool for this WhatsApp ecommerce message. "
                            "Return only JSON: {\"tool\":\"name\",\"reason\":\"short\"}. "
                            "Allowed tools: get_order_status, search_products, get_customer_profile, "
                            "get_policy_or_faq, get_services, search_knowledge, general_reply. "
                            "Use search_knowledge only for normal database lookup."
                        ),
                    },
                    {"role": "user", "content": message},
                ],
                "temperature": 0,
                "max_tokens": 80,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        payload = json.loads(match.group(0) if match else content)
        tool = str(payload.get("tool") or "").strip()
        if tool in TOOL_NAMES:
            return ToolDecision(tool, str(payload.get("reason") or "llm_router"))
    except Exception:
        return None
    return None


def _order_id(message: str) -> str | None:
    match = ORDER_RE.search(message or "")
    return next((group.upper() for group in match.groups() if group), None) if match else None


def _order_status_context(db: Session, phone: str, message: str) -> dict:
    return {
        "context": "Order details are fetched live from the ecommerce API during the WhatsApp order-status flow.",
        "data": [],
    }


def _product_context(db: Session, _phone: str, message: str) -> dict:
    return {
        "context": "Product details are fetched live from Shopify during the WhatsApp product flow.",
        "data": [],
    }


def _customer_context(db: Session, phone: str, _message: str) -> dict:
    return {
        "context": "Customer details are fetched live from the ecommerce API only when a workflow needs them; full customer profiles are not stored in Neon.",
        "data": [],
    }


def _policy_faq_context(db: Session, _phone: str, message: str) -> dict:
    return _product_context(db, _phone, message)


def _service_context(db: Session, _phone: str, message: str) -> dict:
    return _product_context(db, _phone, message)


def _database_hint_context(db: Session, _phone: str, message: str) -> dict:
    return {"context": "", "data": []}


def _general_context(_db: Session, _phone: str, _message: str) -> dict:
    return {"context": "", "data": []}
