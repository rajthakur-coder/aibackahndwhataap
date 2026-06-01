from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.models.audit import AuditLog
from app.models.whatsapp import WebhookEvent, WhatsappCredential
from app.modules.whatsapp.webhooks.events.event_service import (
    UnresolvedWebhookTenantError,
    get_or_create_webhook_event,
    mark_webhook_event_failed,
    should_process_webhook_event,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    WebhookEvent.__table__.create(bind=engine)
    WhatsappCredential.__table__.create(bind=engine)
    AuditLog.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_processed_duplicate_webhook_is_not_processed_again():
    db = _session()
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="12345", status="active"))
    db.commit()
    incoming = {"id": "wamid-1", "phone": "919999999999", "text": "hello", "payload": {"id": "wamid-1"}, "phone_number_id": "12345"}

    event, created = get_or_create_webhook_event(db, incoming, request_id="req-1")
    assert created is True
    assert should_process_webhook_event(event, created) is True

    event.status = "processed"
    db.commit()

    duplicate, duplicate_created = get_or_create_webhook_event(db, incoming, request_id="req-2")

    assert duplicate.id == event.id
    assert duplicate_created is False
    assert should_process_webhook_event(duplicate, duplicate_created) is False


def test_webhook_with_unknown_phone_number_id_fails_closed():
    db = _session()
    incoming = {
        "id": "wamid-credentials-missing",
        "phone": "919999999999",
        "text": "hello",
        "payload": {"id": "wamid-credentials-missing"},
        "phone_number_id": "12345",
    }

    try:
        get_or_create_webhook_event(db, incoming, request_id="req-credentials-missing")
    except UnresolvedWebhookTenantError as exc:
        assert "Could not resolve tenant" in str(exc)
    else:
        raise AssertionError("Expected unresolved webhook tenant to fail closed")


def test_webhook_resolves_tenant_from_phone_number_id():
    db = _session()
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="12345", status="active"))
    db.commit()
    incoming = {
        "id": "wamid-known-credential",
        "phone": "919999999999",
        "text": "hello",
        "payload": {"id": "wamid-known-credential"},
        "phone_number_id": "12345",
    }

    event, created = get_or_create_webhook_event(db, incoming, request_id="req-known-credential")

    assert created is True
    assert event.tenant_id == "brand-a"


def test_webhook_failure_dead_letters_after_max_attempts_and_audits():
    db = _session()
    event = WebhookEvent(
        tenant_id="tenant-a",
        external_id="wamid-2",
        request_id="req-1",
        phone="919999999999",
        message_text="hello",
        status="processing",
        attempts=5,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    mark_webhook_event_failed(db, event, RuntimeError("provider down"))

    audit_log = db.execute(select(AuditLog).where(AuditLog.action == "webhook.whatsapp_failed")).scalars().first()
    assert event.status == "dead_letter"
    assert event.dead_lettered_at is not None
    assert event.next_retry_at is None
    assert audit_log is not None
    assert audit_log.tenant_id == "tenant-a"
    assert audit_log.status == "dead_letter"
