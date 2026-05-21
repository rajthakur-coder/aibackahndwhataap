import json
import re
from dataclasses import dataclass

import requests
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ecommerce import EcommerceCustomer, EcommerceOrder
from app.modules.ecommerce.core.ecommerce_core_service import order_status_text
from app.modules.ai.core.intelligence_service import detect_query_intent


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 20
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*id)?[\s:#-]*#?([A-Za-z0-9-]{2,})\b", re.I)
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
    return match.group(1).upper() if match else None


def _order_status_context(db: Session, phone: str, message: str) -> dict:
    order_id = _order_id(message)
    statement = select(EcommerceOrder)
    if order_id:
        normalized = order_id.lstrip("#")
        statement = statement.where(
            or_(
                EcommerceOrder.order_number == order_id,
                EcommerceOrder.order_number == f"#{normalized}",
                EcommerceOrder.external_id == normalized,
            )
        )
    else:
        statement = statement.where(EcommerceOrder.phone == phone)
    orders = db.execute(
        statement.order_by(EcommerceOrder.updated_at.desc()).limit(3)
    ).scalars().all()
    if not orders:
        return {"context": "No matching ecommerce order was found in database.", "data": []}
    lines = []
    for order in orders:
        lines.append(
            "\n".join(
                [
                    f"Order: {order.order_number}",
                    f"Customer: {order.customer_name or 'unknown'}",
                    f"Phone: {order.phone or 'unknown'}",
                    f"Status: {order.status or 'unknown'}",
                    f"Fulfillment: {order.fulfillment_status or 'unknown'}",
                    f"Payment: {order.financial_status or 'unknown'}",
                    f"Total: {order.total or ''} {order.currency or ''}".strip(),
                    f"Tracking: {order.tracking_url or order.tracking_number or 'not available'}",
                    f"Summary: {order_status_text(order)}",
                ]
            )
        )
    return {"context": "\n\n".join(lines), "data": [{"id": order.id} for order in orders]}


def _product_context(db: Session, _phone: str, message: str) -> dict:
    return {
        "context": "Product details are fetched live from Shopify during the WhatsApp product flow.",
        "data": [],
    }


def _customer_context(db: Session, phone: str, _message: str) -> dict:
    customer = db.execute(
        select(EcommerceCustomer)
        .where(EcommerceCustomer.phone == phone)
        .order_by(EcommerceCustomer.updated_at.desc())
    ).scalars().first()
    if not customer:
        return {"context": "No ecommerce customer profile found for this WhatsApp number.", "data": []}
    context = (
        f"Customer: {customer.name or 'unknown'}\n"
        f"Phone: {customer.phone or 'unknown'}\n"
        f"Email: {customer.email or 'unknown'}\n"
        f"Total orders: {customer.total_orders or 0}\n"
        f"Total spend: {customer.total_spend or 'unknown'}\n"
        f"Tags: {customer.tags or 'none'}\n"
        f"WhatsApp opt-in: {customer.whatsapp_opt_in or 'unknown'}"
    )
    return {"context": context, "data": [{"id": customer.id}]}


def _policy_faq_context(db: Session, _phone: str, message: str) -> dict:
    return _product_context(db, _phone, message)


def _service_context(db: Session, _phone: str, message: str) -> dict:
    return _product_context(db, _phone, message)


def _database_hint_context(db: Session, _phone: str, message: str) -> dict:
    return {"context": "", "data": []}


def _general_context(_db: Session, _phone: str, _message: str) -> dict:
    return {"context": "", "data": []}
