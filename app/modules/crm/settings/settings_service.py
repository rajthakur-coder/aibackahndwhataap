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
from app.models.tenants import TenantConfig
from app.models.whatsapp import Message, WhatsappCredential


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


def clear_bot_settings_cache(tenant_id: str | None = None) -> None:
    if tenant_id is None:
        _bot_settings_cache.clear()
        return
    _bot_settings_cache.pop(normalize_tenant_id(tenant_id), None)

def _snapshot_bot_settings(row: BotSettings) -> SimpleNamespace:
    return SimpleNamespace(
        id=row.id,
        tenant_id=row.tenant_id,
        bot_enabled=row.bot_enabled,
        default_language=row.default_language,
        welcome_message=row.welcome_message,
        fallback_message=row.fallback_message,
        offline_message=row.offline_message,
        ai_personality=row.ai_personality,
        ai_tone=row.ai_tone,
        response_length=row.response_length,
        custom_instructions=row.custom_instructions,
        brand_prompt=row.brand_prompt,
        main_menu_buttons=row.main_menu_buttons,
        handoff_keywords=row.handoff_keywords,
        business_hours_enabled=row.business_hours_enabled,
        business_hours_start=row.business_hours_start,
        business_hours_end=row.business_hours_end,
        timezone=row.timezone,
        updated_at=row.updated_at,
    )

def _cached_bot_settings(tenant_id: str = DEFAULT_TENANT_ID) -> SimpleNamespace | None:
    cache_key = normalize_tenant_id(tenant_id)
    cached = _bot_settings_cache.get(cache_key)
    if not cached:
        return None
    cached_at, settings = cached
    if time.monotonic() - cached_at > BOT_SETTINGS_CACHE_TTL_SECONDS:
        clear_bot_settings_cache(cache_key)
        return None
    return settings

def _store_bot_settings_cache(row: BotSettings) -> SimpleNamespace:
    snapshot = _snapshot_bot_settings(row)
    _bot_settings_cache[normalize_tenant_id(row.tenant_id)] = (time.monotonic(), snapshot)
    return snapshot

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
    return next((group.upper() for group in match.groups() if group), None) if match else None

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

def _load_json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item).strip().lower() for item in data if str(item).strip()] if isinstance(data, list) else []

def _first_real_tenant_id(db: Session) -> str | None:
    row = db.execute(
        select(BotSettings.tenant_id).where(BotSettings.tenant_id != DEFAULT_TENANT_ID)
    ).scalar()
    if row:
        return normalize_tenant_id(row)

    row = db.execute(
        select(WhatsappCredential.tenant_id)
        .where(WhatsappCredential.tenant_id != DEFAULT_TENANT_ID)
        .order_by(WhatsappCredential.updated_at.desc())
    ).scalar()
    if row:
        return normalize_tenant_id(row)

    row = db.execute(
        select(TenantConfig.tenant_id)
        .where(TenantConfig.tenant_id != DEFAULT_TENANT_ID)
        .order_by(TenantConfig.updated_at.desc())
    ).scalar()
    return normalize_tenant_id(row) if row else None

def _resolve_bot_settings_tenant_id(db: Session, tenant_id: str | None = None) -> str:
    resolved = normalize_tenant_id(tenant_id or current_tenant_id())
    if resolved != DEFAULT_TENANT_ID:
        return resolved
    return _first_real_tenant_id(db) or resolved

def get_bot_settings(db: Session, tenant_id: str | None = None) -> BotSettings:
    tenant_id = _resolve_bot_settings_tenant_id(db, tenant_id)
    cached = _cached_bot_settings(tenant_id)
    if cached:
        return cached

    row = db.execute(select(BotSettings).where(BotSettings.tenant_id == tenant_id)).scalars().first()
    if row:
        return _store_bot_settings_cache(row)
    if tenant_id == DEFAULT_TENANT_ID:
        return SimpleNamespace(
            id=None,
            tenant_id=DEFAULT_TENANT_ID,
            bot_enabled="true",
            default_language="english",
            welcome_message="Welcome! How can I help you today?",
            fallback_message="I do not have that information right now. I can connect you with our support team.",
            offline_message="Our support team is offline right now. Your request is noted and the team will reply during business hours.",
            ai_personality="helpful",
            ai_tone="friendly",
            response_length="brief",
            custom_instructions=None,
            main_menu_buttons=None,
            handoff_keywords=json.dumps(["human", "agent", "support", "complaint", "manager"], ensure_ascii=True),
            business_hours_enabled="false",
            business_hours_start="09:00",
            business_hours_end="18:00",
            timezone="Asia/Kolkata",
            updated_at=None,
        )
    row = BotSettings(
        tenant_id=tenant_id,
        default_language="english",
        handoff_keywords=json.dumps(["human", "agent", "support", "complaint", "manager"], ensure_ascii=True),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _store_bot_settings_cache(row)

def bot_setting_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def _matched_handoff_keyword(message: str, keywords: set[str]) -> str | None:
    normalized = " ".join((message or "").lower().split())
    words = _words(message)
    for keyword in sorted(keywords, key=len, reverse=True):
        clean = " ".join(keyword.lower().split())
        if not clean:
            continue
        if " " in clean and clean in normalized:
            return clean
        if clean in words:
            return clean
    return None

__all__ = [
    "clear_bot_settings_cache",
    "_snapshot_bot_settings",
    "_cached_bot_settings",
    "_store_bot_settings_cache",
    "_words",
    "detect_intent",
    "_extract_name",
    "_extract_email",
    "_extract_order_id",
    "_extract_time_hint",
    "_load_json_list",
    "get_bot_settings",
    "bot_setting_enabled",
    "_matched_handoff_keyword",
]
