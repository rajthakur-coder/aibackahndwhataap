import json
import re
import time
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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

def _get_or_create_profile(db: Session, phone: str) -> CustomerProfile:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    profile = db.execute(
        select(CustomerProfile).where(CustomerProfile.tenant_id == tenant_id, CustomerProfile.phone == phone)
    ).scalars().first()
    if profile:
        return profile

    profile = db.execute(
        select(CustomerProfile).where(CustomerProfile.phone == phone)
    ).scalars().first()
    if profile:
        return profile

    profile = CustomerProfile(tenant_id=tenant_id, phone=phone)
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        profile = db.execute(
            select(CustomerProfile).where(CustomerProfile.phone == phone)
        ).scalars().first()
        if profile:
            return profile
        raise
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
        tenant_id=normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    lead = db.execute(
        select(Lead)
        .where(Lead.tenant_id == tenant_id, Lead.phone == phone)
        .order_by(Lead.created_at.desc())
    ).scalars().first()
    if not lead or lead.status in {"closed", "lost"}:
        lead = Lead(tenant_id=tenant_id, phone=phone, source="whatsapp")
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
        tenant_id=normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    ticket = db.execute(
        select(HandoffTicket)
        .where(HandoffTicket.tenant_id == tenant_id, HandoffTicket.phone == phone, HandoffTicket.status == "open")
        .order_by(HandoffTicket.created_at.desc())
    ).scalars().first()
    if ticket:
        return ticket

    recent_messages = db.execute(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.phone == phone)
        .order_by(Message.created_at.desc())
        .limit(6)
    ).scalars().all()
    summary = "\n".join(
        f"{message.direction}: {message.message}"
        for message in reversed(recent_messages)
    )
    ticket = HandoffTicket(tenant_id=tenant_id, phone=phone, reason=reason, summary=summary)
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
    exists = db.execute(
        select(CustomerMemory).where(
            CustomerMemory.tenant_id == normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID),
            CustomerMemory.phone == phone,
            CustomerMemory.memory_type == memory_type,
            CustomerMemory.content == content,
        )
    ).scalars().first()
    if exists:
        return
    db.add(CustomerMemory(tenant_id=normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID), phone=phone, memory_type=memory_type, content=content[:1000]))
    db.commit()

__all__ = [
    "_get_or_create_profile",
    "_log_action",
    "_upsert_lead",
    "_create_appointment",
    "_open_handoff",
    "_remember_customer",
]
