import json
import re
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ai.intelligence.intelligence_service import detect_query_intent
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id
from app.models.crm import (
    AgentAction,
    Appointment,
    BotSettings,
    CustomerMemory,
    CustomerProfile,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.models.ecommerce import EcommerceOrder
from app.models.whatsapp import Message


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b", re.I)
NAME_RE = re.compile(r"\b(?:my name is|i am|i'm|name is|mera naam)\s+([A-Za-z][A-Za-z ]{1,40})", re.I)

INTENT_KEYWORDS = {
    "appointment_booking": {
        "appointment",
        "book",
        "booking",
        "call",
        "demo",
        "meeting",
        "schedule",
        "slot",
        "visit",
    },
    "order_status": {
        "delivery",
        "invoice",
        "order",
        "shipment",
        "status",
        "track",
        "tracking",
    },
    "human_handoff": {
        "agent",
        "complaint",
        "human",
        "manager",
        "person",
        "representative",
        "support",
    },
    "pricing": {
        "cost",
        "fees",
        "package",
        "plan",
        "price",
        "pricing",
        "rate",
    },
    "lead": {
        "buy",
        "contact",
        "demo",
        "interested",
        "quote",
        "service",
        "want",
    },
    "payment_link": {
        "pay",
        "payment",
        "payment link",
        "upi",
    },
}
BOT_SETTINGS_CACHE_TTL_SECONDS = 30
_bot_settings_cache: dict[str, tuple[float, SimpleNamespace]] = {}


from app.modules.crm.settings.settings_service import *
from app.modules.crm.agent.action_service import *

def _phone_lookup_values(phone: str) -> list[str]:
    raw = (phone or "").strip()
    digits = re.sub(r"\D+", "", raw)
    values = [raw]
    if digits:
        values.extend([digits, f"+{digits}"])
        if len(digits) > 10:
            values.append(digits[-10:])
            values.append(f"+{digits[-10:]}")
    return list(dict.fromkeys(value for value in values if value))

def _load_order_items(order: EcommerceOrder) -> list[dict]:
    if not order.items:
        return []
    try:
        items = json.loads(order.items)
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []

def _latest_ecommerce_orders(db: Session, phone: str) -> list[EcommerceOrder]:
    values = _phone_lookup_values(phone)
    if not values:
        return []
    return db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.phone.in_(values))
        .order_by(EcommerceOrder.updated_at.desc())
        .limit(3)
    ).scalars().all()

def _ecommerce_order_context(orders: list[EcommerceOrder]) -> str:
    if not orders:
        return ""

    lines = []
    purchased_items = []
    for order in orders:
        items = _load_order_items(order)
        item_names = [
            str(item.get("name") or item.get("title") or "").strip()
            for item in items
            if isinstance(item, dict) and (item.get("name") or item.get("title"))
        ][:3]
        purchased_items.extend(item_names)
        status = order.fulfillment_status or order.status or "unknown"
        total = " ".join(filter(None, [order.total, order.currency])).strip() or "unknown"
        tracking = order.tracking_number or order.courier_company or ""
        line = f"{order.order_number}: status={status}, total={total}"
        if item_names:
            line += f", items={', '.join(item_names)}"
        if tracking:
            line += f", tracking={tracking}"
        lines.append(line)

    preference_bits = []
    if purchased_items:
        preference_bits.append("previously_bought=" + ", ".join(dict.fromkeys(purchased_items[:6])))

    return "\n".join(
        [
            "Latest ecommerce orders: " + " | ".join(lines),
            "Ecommerce personalization hints: " + "; ".join(preference_bits),
        ]
    ).strip()

def get_customer_context(db: Session, phone: str) -> str:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    profile = db.execute(
        select(CustomerProfile).where(CustomerProfile.phone == phone)
    ).scalars().first()
    memories = db.execute(
        select(CustomerMemory)
        .where(CustomerMemory.phone == phone)
        .order_by(CustomerMemory.created_at.desc())
        .limit(6)
    ).scalars().all()
    lead = db.execute(
        select(Lead)
        .where(Lead.tenant_id == tenant_id, Lead.phone == phone)
        .order_by(Lead.created_at.desc())
    ).scalars().first()

    parts = []
    if profile:
        parts.append(
            "Customer profile: "
            f"name={profile.name or 'unknown'}, email={profile.email or 'unknown'}, "
            f"last_intent={profile.intent or 'unknown'}, status={profile.status or 'active'}"
        )
    if lead:
        parts.append(
            "Lead: "
            f"status={lead.status}, intent={lead.intent or 'unknown'}, "
            f"name={lead.name or 'unknown'}, email={lead.email or 'unknown'}"
        )
    if memories:
        memory_text = "; ".join(f"{memory.memory_type}: {memory.content}" for memory in memories)
        parts.append(f"Customer memory: {memory_text}")
    ecommerce_context = _ecommerce_order_context(_latest_ecommerce_orders(db, phone))
    if ecommerce_context:
        parts.append(ecommerce_context)

    return "\n".join(parts)

__all__ = [
    "_phone_lookup_values",
    "_load_order_items",
    "_latest_ecommerce_orders",
    "_ecommerce_order_context",
    "get_customer_context",
]
