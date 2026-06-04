from app.modules.whatsapp.client.credentials import WhatsappClientCredentials
from app.modules.whatsapp.client.interactive_client_service import send_whatsapp_carousel


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


def test_carousel_sends_up_to_ten_cards(monkeypatch):
    captured = {}

    def fake_credentials():
        return WhatsappClientCredentials(access_token="token", phone_number_id="12345")

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _Response()

    monkeypatch.setattr(
        "app.modules.whatsapp.client.interactive_client_service.resolve_whatsapp_client_credentials",
        fake_credentials,
    )
    monkeypatch.setattr("app.modules.whatsapp.client.interactive_client_service.requests.post", fake_post)

    products = [
        {
            "title": f"Product {index}",
            "image_url": f"https://example.com/product-{index}.jpg",
            "product_url": f"https://example.com/product-{index}",
            "price_min": "999",
        }
        for index in range(12)
    ]

    send_whatsapp_carousel("919999999999", products, "These are the top-selling products.")

    cards = captured["payload"]["interactive"]["action"]["cards"]
    assert len(cards) == 10
    assert cards[0]["card_index"] == 0
    assert cards[-1]["card_index"] == 9
