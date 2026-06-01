import json
import re
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.ai.intelligence.intelligence_service import detect_query_intent
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id
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


from app.modules.crm.agent.action_service import *
from app.modules.crm.agent.context_service import *
from app.modules.crm.settings.settings_service import *



















































def process_agent_message(db: Session, phone: str, message: str, tenant_id: str | None = None) -> dict:
    intent = detect_intent(message)
    bot_settings = get_bot_settings(db, tenant_id=tenant_id)
    custom_handoff_keywords = set(_load_json_list(bot_settings.handoff_keywords))
    matched_handoff_keyword = _matched_handoff_keyword(message, custom_handoff_keywords)
    if matched_handoff_keyword:
        intent = "human_handoff"
    query_intent = detect_query_intent(message)
    name = _extract_name(message)
    email = _extract_email(message)
    order_id = _extract_order_id(message)
    time_hint = _extract_time_hint(message)

    profile = _get_or_create_profile(db, phone)
    profile.name = name or profile.name
    profile.email = email or profile.email
    profile.intent = intent
    db.commit()

    if name:
        _remember_customer(db, phone, "name", name)
    if email:
        _remember_customer(db, phone, "email", email)

    reply_override = None
    action_results = []

    if intent in {"lead", "pricing", "payment_link", "appointment_booking"} or name or email:
        lead = _upsert_lead(db, phone, intent, profile.name, profile.email, message)
        action_results.append({"lead_id": lead.id, "lead_status": lead.status})

    if intent == "appointment_booking":
        appointment = _create_appointment(db, phone, profile.name, time_hint, message)
        _log_action(
            db,
            "appointment_requested",
            phone=phone,
            payload={"appointment_id": appointment.id, "requested_time": time_hint},
        )
        action_results.append({"appointment_id": appointment.id, "appointment_status": appointment.status})

    if intent == "order_status":
        if order_id:
            order = db.execute(
                select(OrderStatus).where(OrderStatus.order_id == order_id)
            ).scalars().first()
            if order:
                reply_override = f"Your order {order.order_id} status is: {order.status}. {order.details or ''}".strip()
            else:
                reply_override = (
                    f"I could not find order {order_id}. Please check the order ID, "
                    "or I can connect you with the team."
                )
            action_results.append({"order_id": order_id, "found": bool(order)})
        else:
            reply_override = "Please share your order ID so I can check the status."

    if intent == "human_handoff":
        handoff_reason = f"matched_keyword:{matched_handoff_keyword}" if matched_handoff_keyword else "customer_requested_human"
        ticket = _open_handoff(db, phone, handoff_reason)
        _log_action(
            db,
            "human_handoff",
            phone=phone,
            payload={"ticket_id": ticket.id, "reason": ticket.reason, "matched_keyword": matched_handoff_keyword},
        )
        reply_override = (
            f"I am connecting you with our support team. Your ticket ID is #{ticket.id}. "
            "I will pause automated replies while this ticket is open."
        )
        action_results.append({"handoff_ticket_id": ticket.id})

    if intent == "payment_link":
        _log_action(
            db,
            "payment_link_requested",
            phone=phone,
            payload={"message": message},
            result={"status": "needs_payment_gateway_configuration"},
        )
        action_results.append({"payment_link": "needs_payment_gateway_configuration"})

    return {
        "intent": intent,
        "query_intent": query_intent.name,
        "policy_type": query_intent.policy_type,
        "name": profile.name,
        "email": profile.email,
        "reply_override": reply_override,
        "context": get_customer_context(db, phone),
        "actions": action_results,
        "processed_at": datetime.utcnow().isoformat(),
    }


def log_crm_update(db: Session, phone: str, payload: dict) -> AgentAction:
    return _log_action(db, "crm_update", phone=phone, payload=payload)


def log_email_request(db: Session, phone: str, payload: dict) -> AgentAction:
    return _log_action(db, "email_send", phone=phone, payload=payload, status="queued")


def log_payment_link_request(db: Session, phone: str, payload: dict) -> AgentAction:
    return _log_action(
        db,
        "payment_link",
        phone=phone,
        payload=payload,
        result={"status": "configure_gateway_to_send_real_links"},
    )

