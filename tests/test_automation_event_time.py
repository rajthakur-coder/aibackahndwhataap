from datetime import datetime, timezone

from app.models.automation import MessageTemplate
from app.modules.automation.events import event_service
from app.modules.automation.events.event_service import _db_naive, _utcnow_like
from app.modules.automation.runtime import sync_service


def test_automation_event_time_helpers_match_datetime_awareness():
    aware = datetime.now(timezone.utc)
    naive = datetime.utcnow()

    assert _utcnow_like(aware).tzinfo is not None
    assert _utcnow_like(naive).tzinfo is None
    assert _db_naive(aware).tzinfo is None


def test_sync_service_exports_template_button_parameters():
    assert callable(sync_service._template_button_parameters)
    assert callable(sync_service._template_body_parameters)


def test_automation_send_passes_event_tenant_to_whatsapp_template(monkeypatch):
    captured = {}

    def fake_send_template(*args, **kwargs):
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(event_service, "send_whatsapp_template", fake_send_template)
    template = MessageTemplate(
        tenant_id="brand-a",
        name="abandoned_cart_recovery",
        provider_template_name="abandoned_cart_recovery",
        template_type="whatsapp_template",
        language="en",
        body="Hi {{customer_name}}",
        body_variable_order='["customer_name"]',
    )

    import asyncio

    result = asyncio.run(
        event_service._send_message(
            template,
            "919999999999",
            "Hi Riya",
            {"customer_name": "Riya", "trigger": "cart_abandoned", "cart_token": "abc"},
            "brand-a",
        )
    )

    assert result == {"ok": True}
    assert captured["args"][-1] == "brand-a"


def test_cart_abandoned_template_without_button_does_not_send_button_parameter():
    template = MessageTemplate(
        name="wa:abandoned_checkout_message:en_US",
        provider_template_name="abandoned_checkout_message",
        body="Hi {{1}}, Looks like you left our bestseller {{2}} ...",
        body_variable_order='["customer_name", "cart_url"]',
    )
    context = {
        "trigger": "cart_abandoned",
        "customer_name": "Riya",
        "cart_url": "https://shopify.test/checkouts/abc",
        "items": [{"presentment_title": "Phone Case"}],
    }

    assert sync_service._template_body_parameters(template, context) == ["Riya", "Phone Case"]
    assert sync_service._template_button_parameters(template, context) == []
