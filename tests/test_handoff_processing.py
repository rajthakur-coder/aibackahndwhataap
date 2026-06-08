import asyncio

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.crm import AgentAction, HandoffTicket
from app.models.whatsapp import WebhookEvent
from app.modules.whatsapp.webhooks.observability.timing_service import WebhookTiming
from app.modules.whatsapp.webhooks.processing.handoff_state import _handle_active_handoff


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        HandoffTicket.__table__,
        AgentAction.__table__,
        WebhookEvent.__table__,
    ):
        table.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()


def test_active_handoff_records_message_without_blocking_bot_flow():
    db = _session()
    phone = "919999999999"
    event = WebhookEvent(tenant_id="brand-a", phone=phone, message_text="show catalog")
    ticket = HandoffTicket(
        tenant_id="brand-a",
        phone=phone,
        reason="need human support",
        status="open",
        summary="incoming: need human support",
    )
    db.add_all([event, ticket])
    db.commit()
    db.refresh(event)
    db.refresh(ticket)

    handled = asyncio.run(
        _handle_active_handoff(
            db,
            event,
            phone,
            "show catalog",
            bot_settings=object(),
            timing=WebhookTiming(db, phone, event.id),
        )
    )

    db.refresh(ticket)
    action = db.query(AgentAction).filter_by(action_type="handoff_message_received").one()

    assert handled is False
    assert "incoming: show catalog" in ticket.summary
    assert action.status == "open"
