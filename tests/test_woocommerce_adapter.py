from app.models.ecommerce import EcommerceConnection
from app.modules.headless.oms_adapter import WooCommerceOMSAdapter


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _connection():
    return EcommerceConnection(
        tenant_id="brand-a",
        name="Woo",
        platform="woocommerce",
        store_url="https://store.example",
        consumer_key="ck_test",
        consumer_secret="cs_test",
    )


def test_woocommerce_checkout_creates_pending_order(monkeypatch):
    calls = {}

    def fake_post(url, auth, json, timeout):
        calls.update({"url": url, "auth": auth, "json": json, "timeout": timeout})
        return _Response({"id": 42, "order_key": "wc_order_key"})

    monkeypatch.setattr("app.modules.headless.oms_adapter.requests.post", fake_post)

    result = WooCommerceOMSAdapter(_connection()).create_draft_order(
        [{"external_id": "101", "variant_id": "202", "qty": 2}],
        {"phone": "919999999999", "email": "buyer@example.com", "discount": {"code": "FIRST10"}},
    )

    assert calls["url"] == "https://store.example/wp-json/wc/v3/orders"
    assert calls["auth"] == ("ck_test", "cs_test")
    assert calls["json"]["status"] == "pending"
    assert calls["json"]["line_items"] == [{"product_id": 101, "quantity": 2, "variation_id": 202}]
    assert calls["json"]["coupon_lines"] == [{"code": "FIRST10"}]
    assert result["checkout_url"] == "https://store.example/checkout/order-pay/42/?pay_for_order=true&key=wc_order_key"


def test_woocommerce_return_updates_order_note(monkeypatch):
    calls = {}

    def fake_put(url, auth, json, timeout):
        calls.update({"url": url, "auth": auth, "json": json, "timeout": timeout})
        return _Response({"id": 42, "customer_note": json["customer_note"]})

    monkeypatch.setattr("app.modules.headless.oms_adapter.requests.put", fake_put)

    result = WooCommerceOMSAdapter(_connection()).initiate_return("#42", [{"sku": "SKU-1"}], "damaged")

    assert calls["url"] == "https://store.example/wp-json/wc/v3/orders/42"
    assert calls["auth"] == ("ck_test", "cs_test")
    assert "damaged" in calls["json"]["note"]
    assert result["id"] == 42
