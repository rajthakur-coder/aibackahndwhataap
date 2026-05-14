import json
import re
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.ecommerce import find_order_for_customer, order_status_text
from app.services.intelligence import detect_query_intent
from app.models.entities import (
    AgentAction,
    Appointment,
    CustomerMemory,
    CustomerProfile,
    HandoffTicket,
    Lead,
    Message,
    OrderStatus,
)


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
ORDER_RE = re.compile(r"\b(?:order|ord|booking|invoice)(?:\s*id)?[\s:#-]*#?([A-Za-z0-9-]{2,})\b", re.I)
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


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def detect_intent(message: str) -> str:
    query_intent = detect_query_intent(message)
    if query_intent.name == "tracking_question":
        return "order_status"
    if query_intent.name == "price_question":
        return "pricing"
    if query_intent.name == "policy_question":
        return "policy_question"
    if query_intent.name == "catalog_request":
        return "catalog_request"
    if query_intent.name == "image_request":
        return "image_request"

    text = message.lower()
    words = _words(message)
    best_intent = "general"
    best_score = 0

    for intent, keywords in INTENT_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if " " in keyword:
                score += 2 if keyword in text else 0
            elif keyword in words:
                score += 1

        if score > best_score:
            best_intent = intent
            best_score = score

    return best_intent


def _extract_name(message: str) -> str | None:
    match = NAME_RE.search(message)
    if not match:
        return None
    return " ".join(match.group(1).split()).strip()


def _extract_email(message: str) -> str | None:
    match = EMAIL_RE.search(message)
    return match.group(0).lower() if match else None


def _extract_order_id(message: str) -> str | None:
    match = ORDER_RE.search(message)
    return match.group(1).upper() if match else None


def _extract_time_hint(message: str) -> str | None:
    text = message.strip()
    lower = text.lower()
    markers = ["today", "tomorrow", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if any(marker in lower for marker in markers):
        return text[:120]

    time_match = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", lower)
    if time_match:
        return text[:120]

    return None


def _get_or_create_profile(db: Session, phone: str) -> CustomerProfile:
    profile = db.query(CustomerProfile).filter(CustomerProfile.phone == phone).first()
    if profile:
        return profile

    profile = CustomerProfile(phone=phone)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _log_action(
    db: Session,
    action_type: str,
    phone: str | None = None,
    status: str = "logged",
    payload: dict | None = None,
    result: dict | None = None,
) -> AgentAction:
    action = AgentAction(
        phone=phone,
        action_type=action_type,
        status=status,
        payload=json.dumps(payload or {}),
        result=json.dumps(result or {}),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _upsert_lead(
    db: Session,
    phone: str,
    intent: str,
    name: str | None,
    email: str | None,
    message: str,
) -> Lead:
    lead = (
        db.query(Lead)
        .filter(Lead.phone == phone)
        .order_by(Lead.created_at.desc())
        .first()
    )
    if not lead or lead.status in {"closed", "lost"}:
        lead = Lead(phone=phone, source="whatsapp")
        db.add(lead)

    lead.intent = intent
    lead.name = name or lead.name
    lead.email = email or lead.email
    lead.notes = "\n".join(filter(None, [lead.notes, message]))[-3000:]
    lead.status = "qualified" if email or name else lead.status
    db.commit()
    db.refresh(lead)
    return lead


def _create_appointment(
    db: Session,
    phone: str,
    name: str | None,
    time_hint: str | None,
    message: str,
) -> Appointment:
    appointment = Appointment(
        phone=phone,
        customer_name=name,
        requested_time=time_hint,
        notes=message,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def _open_handoff(
    db: Session,
    phone: str,
    reason: str,
) -> HandoffTicket:
    ticket = (
        db.query(HandoffTicket)
        .filter(HandoffTicket.phone == phone, HandoffTicket.status == "open")
        .order_by(HandoffTicket.created_at.desc())
        .first()
    )
    if ticket:
        return ticket

    recent_messages = (
        db.query(Message)
        .filter(Message.phone == phone)
        .order_by(Message.created_at.desc())
        .limit(6)
        .all()
    )
    summary = "\n".join(
        f"{message.direction}: {message.message}"
        for message in reversed(recent_messages)
    )
    ticket = HandoffTicket(phone=phone, reason=reason, summary=summary)
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


def _remember_customer(
    db: Session,
    phone: str,
    memory_type: str,
    content: str,
) -> None:
    if not content:
        return
    exists = (
        db.query(CustomerMemory)
        .filter(CustomerMemory.phone == phone, CustomerMemory.memory_type == memory_type, CustomerMemory.content == content)
        .first()
    )
    if exists:
        return
    db.add(CustomerMemory(phone=phone, memory_type=memory_type, content=content[:1000]))
    db.commit()


def get_customer_context(db: Session, phone: str) -> str:
    profile = db.query(CustomerProfile).filter(CustomerProfile.phone == phone).first()
    memories = (
        db.query(CustomerMemory)
        .filter(CustomerMemory.phone == phone)
        .order_by(CustomerMemory.created_at.desc())
        .limit(6)
        .all()
    )
    lead = (
        db.query(Lead)
        .filter(Lead.phone == phone)
        .order_by(Lead.created_at.desc())
        .first()
    )

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

    return "\n".join(parts)


def process_agent_message(db: Session, phone: str, message: str) -> dict:
    intent = detect_intent(message)
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
        ecommerce_order = find_order_for_customer(db, phone, order_id)
        if ecommerce_order:
            reply_override = order_status_text(ecommerce_order)
            action_results.append(
                {
                    "ecommerce_order_id": ecommerce_order.id,
                    "order_id": ecommerce_order.order_number,
                    "found": True,
                }
            )
        elif order_id:
            order = db.query(OrderStatus).filter(OrderStatus.order_id == order_id).first()
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
        ticket = _open_handoff(db, phone, "customer_requested_human")
        _log_action(
            db,
            "human_handoff",
            phone=phone,
            payload={"ticket_id": ticket.id, "reason": ticket.reason},
        )
        reply_override = (
            f"I have created a human support ticket #{ticket.id}. "
            "Our team will review this conversation and contact you."
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

