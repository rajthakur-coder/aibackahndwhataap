import json
import os
import re
from dataclasses import dataclass

import requests
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceCustomer, EcommerceOrder, EcommerceProduct
from app.models.entities import FAQ, Policy, Service, StructuredProduct
from app.services.ecommerce import order_status_text
from app.services.intelligence import detect_query_intent


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
REQUEST_TIMEOUT = 20
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)[\s:#-]*#?([A-Za-z0-9-]{2,})\b", re.I)
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
    return ToolDecision("search_knowledge", "fallback knowledge search")


def run_ai_tool(db: Session, phone: str, message: str, decision: ToolDecision | None = None) -> dict:
    decision = decision or decide_tool_for_message(message)
    handlers = {
        "get_order_status": _order_status_context,
        "search_products": _product_context,
        "get_customer_profile": _customer_context,
        "get_policy_or_faq": _policy_faq_context,
        "get_services": _service_context,
        "search_knowledge": _knowledge_hint_context,
        "general_reply": _general_context,
    }
    handler = handlers.get(decision.name, _knowledge_hint_context)
    result = handler(db, phone, message)
    return {
        "tool": decision.name,
        "reason": decision.reason,
        "context": result.get("context", ""),
        "data": result.get("data", []),
        "needs_rag": result.get("needs_rag", False),
    }


def _llm_tool_decision(message: str) -> ToolDecision | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    try:
        response = requests.post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", ""),
                "X-Title": os.getenv("APP_NAME", "AI WhatsApp Automation"),
            },
            json={
                "model": os.getenv("ROUTER_MODEL", os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Choose one tool for this WhatsApp ecommerce message. "
                            "Return only JSON: {\"tool\":\"name\",\"reason\":\"short\"}. "
                            "Allowed tools: get_order_status, search_products, get_customer_profile, "
                            "get_policy_or_faq, get_services, search_knowledge, general_reply."
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
    query = db.query(EcommerceOrder)
    if order_id:
        normalized = order_id.lstrip("#")
        query = query.filter(
            or_(
                EcommerceOrder.order_number == order_id,
                EcommerceOrder.order_number == f"#{normalized}",
                EcommerceOrder.external_id == normalized,
            )
        )
    else:
        query = query.filter(EcommerceOrder.phone == phone)
    orders = query.order_by(EcommerceOrder.updated_at.desc()).limit(3).all()
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
    terms = [term for term in re.findall(r"[a-zA-Z0-9]+", message.lower()) if len(term) > 2]
    query = db.query(EcommerceProduct)
    if terms:
        filters = []
        for term in terms[:6]:
            like = f"%{term}%"
            filters.extend(
                [
                    EcommerceProduct.title.ilike(like),
                    EcommerceProduct.description.ilike(like),
                    EcommerceProduct.tags.ilike(like),
                    EcommerceProduct.product_type.ilike(like),
                    EcommerceProduct.vendor.ilike(like),
                    EcommerceProduct.sku.ilike(like),
                ]
            )
        query = query.filter(or_(*filters))
    products = query.order_by(EcommerceProduct.updated_at.desc()).limit(6).all()
    if not products:
        return {"context": "No matching products found in ecommerce database.", "data": [], "needs_rag": True}
    lines = []
    for product in products:
        price = product.price_min or ""
        if product.price_max and product.price_max != product.price_min:
            price = f"{product.price_min or ''} - {product.price_max}"
        lines.append(
            "\n".join(
                [
                    f"Product: {product.title}",
                    f"Price: {price} {product.currency or ''}".strip(),
                    f"Vendor: {product.vendor or 'unknown'}",
                    f"Type: {product.product_type or 'unknown'}",
                    f"SKU: {product.sku or 'unknown'}",
                    f"Inventory: {product.inventory or 'unknown'}",
                    f"URL: {product.product_url or 'not available'}",
                    f"Description: {(product.description or '')[:700]}",
                ]
            )
        )
    return {"context": "\n\n".join(lines), "data": [{"id": product.id} for product in products]}


def _customer_context(db: Session, phone: str, _message: str) -> dict:
    customer = (
        db.query(EcommerceCustomer)
        .filter(EcommerceCustomer.phone == phone)
        .order_by(EcommerceCustomer.updated_at.desc())
        .first()
    )
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
    terms = [term for term in re.findall(r"[a-zA-Z0-9]+", message.lower()) if len(term) > 2]
    sections = []
    if terms:
        filters = []
        for term in terms[:6]:
            like = f"%{term}%"
            filters.extend([Policy.title.ilike(like), Policy.content.ilike(like), Policy.policy_type.ilike(like)])
        policies = db.query(Policy).filter(or_(*filters)).order_by(Policy.created_at.desc()).limit(4).all()
    else:
        policies = db.query(Policy).order_by(Policy.created_at.desc()).limit(4).all()
    for row in policies:
        sections.append(f"Policy: {row.title or row.policy_type}\nType: {row.policy_type}\n{row.content[:1000]}")

    faq_filters = []
    for term in terms[:6]:
        like = f"%{term}%"
        faq_filters.extend([FAQ.question.ilike(like), FAQ.answer.ilike(like), FAQ.category.ilike(like)])
    faqs = db.query(FAQ).filter(or_(*faq_filters)).order_by(FAQ.created_at.desc()).limit(4).all() if faq_filters else []
    for row in faqs:
        sections.append(f"FAQ: {row.question}\nAnswer: {row.answer[:800]}")

    return {"context": "\n\n".join(sections), "data": [], "needs_rag": not bool(sections)}


def _service_context(db: Session, _phone: str, message: str) -> dict:
    terms = [term for term in re.findall(r"[a-zA-Z0-9]+", message.lower()) if len(term) > 2]
    query = db.query(Service)
    if terms:
        filters = []
        for term in terms[:6]:
            like = f"%{term}%"
            filters.extend([Service.name.ilike(like), Service.description.ilike(like), Service.category.ilike(like)])
        query = query.filter(or_(*filters))
    rows = query.order_by(Service.created_at.desc()).limit(5).all()
    sections = [f"Service: {row.name}\nPrice: {row.price or 'not listed'}\n{row.description or ''}" for row in rows]
    return {"context": "\n\n".join(sections), "data": [], "needs_rag": not bool(sections)}


def _knowledge_hint_context(db: Session, _phone: str, message: str) -> dict:
    structured = []
    products = db.query(StructuredProduct).order_by(StructuredProduct.created_at.desc()).limit(3).all()
    for row in products:
        structured.append(f"Structured product: {row.title}\nPrice: {row.price or 'not listed'}\n{row.description or ''}")
    return {"context": "\n\n".join(structured), "data": [], "needs_rag": True}


def _general_context(_db: Session, _phone: str, _message: str) -> dict:
    return {"context": "", "data": [], "needs_rag": False}
