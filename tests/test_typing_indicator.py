from app.models.whatsapp import WebhookEvent
from app.modules.whatsapp.webhooks.tasks.background_service import start_mark_read_with_typing


def test_typing_indicator_uses_event_tenant(monkeypatch):
    calls = []

    def fake_mark_read_with_typing(message_id, tenant_id=None):
        calls.append({"message_id": message_id, "tenant_id": tenant_id})
        return {"success": True}

    monkeypatch.setattr(
        "app.modules.whatsapp.webhooks.tasks.background_service.mark_whatsapp_message_read_with_typing",
        fake_mark_read_with_typing,
    )

    event = WebhookEvent(
        tenant_id="brand-a",
        phone="919999999999",
        external_id="wamid-test",
    )

    handle = start_mark_read_with_typing(event, wait_seconds=1.0)
    if handle:
        handle.stop()

    assert calls == [{"message_id": "wamid-test", "tenant_id": "brand-a"}]
