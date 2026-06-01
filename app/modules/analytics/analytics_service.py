from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.compliance import CSATResponse
from app.models.crm import HandoffTicket, Lead
from app.models.ecommerce import EcommerceCart, EcommerceOrder
from app.models.whatsapp import Message, WebhookEvent, WhatsappInteractionEvent
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def commerce_dashboard(db: Session, tenant_id: str = DEFAULT_TENANT_ID, days: int = 30) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 365)))
    incoming = _count(db, select(Message).where(Message.tenant_id == tenant_id, Message.direction == "incoming", Message.created_at >= since))
    outgoing = _count(db, select(Message).where(Message.tenant_id == tenant_id, Message.direction == "outgoing", Message.created_at >= since))
    tickets = _count(db, select(HandoffTicket).where(HandoffTicket.tenant_id == tenant_id, HandoffTicket.created_at >= since))
    carts = _count(db, select(EcommerceCart).where(EcommerceCart.tenant_id == tenant_id, EcommerceCart.created_at >= since))
    recovered_carts = _count(db, select(EcommerceCart).where(EcommerceCart.tenant_id == tenant_id, EcommerceCart.status == "checkout_ready", EcommerceCart.created_at >= since))
    bulk_leads = _count(db, select(Lead).where(Lead.tenant_id == tenant_id, Lead.intent == "bulk_gifting", Lead.created_at >= since))
    converted_bulk = _count(db, select(Lead).where(Lead.tenant_id == tenant_id, Lead.intent == "bulk_gifting", Lead.email.is_not(None), Lead.created_at >= since))
    orders = db.execute(select(EcommerceOrder).where(EcommerceOrder.tenant_id == tenant_id, EcommerceOrder.created_at >= since)).scalars().all()
    bot_revenue = sum(_money(order.total) for order in orders if _looks_bot_attributed(order))
    cross_sell_events = _count(db, select(WhatsappInteractionEvent).where(WhatsappInteractionEvent.source.in_(["carousel", "cta_url"]), WhatsappInteractionEvent.created_at >= since))
    csat_avg = db.scalar(select(func.avg(CSATResponse.rating)).where(CSATResponse.tenant_id == tenant_id, CSATResponse.created_at >= since)) or 0

    first_response_seconds = _avg_first_response_seconds(db, tenant_id, since)
    resolution_seconds = _avg_resolution_seconds(db, tenant_id, since)
    resolved_by_bot = max(0, incoming - tickets)
    return {
        "tenant_id": tenant_id,
        "period_days": days,
        "conversation_deflection_rate": _ratio(resolved_by_bot, incoming),
        "first_response_time_seconds": round(first_response_seconds, 2),
        "resolution_time_seconds": round(resolution_seconds, 2),
        "bot_attributed_revenue": round(bot_revenue, 2),
        "cart_recovery_rate": _ratio(recovered_carts, carts),
        "cross_sell_attach_rate": _ratio(cross_sell_events, max(outgoing, 1)),
        "bulk_lead_conversion_rate": _ratio(converted_bulk, bulk_leads),
        "csat_average": round(float(csat_avg), 2),
        "counts": {
            "incoming_messages": incoming,
            "outgoing_messages": outgoing,
            "support_tickets": tickets,
            "carts": carts,
            "checkout_ready_carts": recovered_carts,
            "bulk_leads": bulk_leads,
        },
    }


def record_csat(db: Session, tenant_id: str, phone: str, rating: int, comment: str | None = None, conversation_id: str | None = None) -> dict:
    row = CSATResponse(
        tenant_id=normalize_tenant_id(tenant_id),
        phone=phone,
        rating=max(1, min(int(rating), 5)),
        comment=comment,
        conversation_id=conversation_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "rating": row.rating, "phone": row.phone}


def _count(db: Session, statement) -> int:
    return db.scalar(select(func.count()).select_from(statement.subquery())) or 0


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator or 0) / float(denominator or 1), 4)


def _money(value) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def _looks_bot_attributed(order: EcommerceOrder) -> bool:
    text = " ".join(str(value or "").lower() for value in (order.tags, order.note, order.raw_payload))
    return "whatsapp" in text or "ai-agent" in text


def _avg_first_response_seconds(db: Session, tenant_id: str, since: datetime) -> float:
    phones = db.execute(select(distinct(Message.phone)).where(Message.tenant_id == tenant_id, Message.created_at >= since)).scalars().all()
    durations = []
    for phone in phones:
        incoming = db.execute(select(Message).where(Message.tenant_id == tenant_id, Message.phone == phone, Message.direction == "incoming", Message.created_at >= since).order_by(Message.created_at.asc()).limit(1)).scalars().first()
        if not incoming:
            continue
        outgoing = db.execute(select(Message).where(Message.tenant_id == tenant_id, Message.phone == phone, Message.direction == "outgoing", Message.created_at >= incoming.created_at).order_by(Message.created_at.asc()).limit(1)).scalars().first()
        if outgoing:
            durations.append((outgoing.created_at - incoming.created_at).total_seconds())
    return sum(durations) / len(durations) if durations else 0.0


def _avg_resolution_seconds(db: Session, tenant_id: str, since: datetime) -> float:
    events = db.execute(select(WebhookEvent).where(WebhookEvent.tenant_id == tenant_id, WebhookEvent.created_at >= since, WebhookEvent.processed_at.is_not(None))).scalars().all()
    durations = [(event.processed_at - event.created_at).total_seconds() for event in events if event.processed_at and event.created_at]
    return sum(durations) / len(durations) if durations else 0.0
