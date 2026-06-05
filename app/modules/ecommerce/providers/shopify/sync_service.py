import json

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm import AgentAction
from app.models.ecommerce import EcommerceConnection
from app.modules.ecommerce.providers.shopify.client_service import (
    _shopify_request,
    fetch_abandoned_checkouts,
    test_connection,
)

SHOPIFY_WEBHOOK_TOPICS = {
    "orders/create": "/webhooks/shopify/orders",
    "orders/updated": "/webhooks/shopify/orders",
    "fulfillments/create": "/webhooks/shopify/fulfillments",
    "fulfillments/update": "/webhooks/shopify/fulfillments",
    "products/create": "/webhooks/shopify/products",
    "products/update": "/webhooks/shopify/products",
}


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True)

def sync_abandoned_checkouts(db: Session, connection: EcommerceConnection, limit: int = 50, *, force: bool = False) -> dict:
    if not force and not settings.ECOMMERCE_AUTO_SYNC_CHECKOUTS_ENABLED:
        return {
            "status": "skipped",
            "reason": "checkout_auto_sync_disabled",
            "message": "Shopify abandoned checkout sync is paused. Use /ecommerce/abandoned-cart for manual tests.",
            "connection_id": connection.id,
        }

    if connection.platform != "shopify":
        return {"status": "skipped", "reason": "checkout sync is only available for Shopify"}

    from app.modules.automation.runtime.sync_service import create_abandoned_cart_event

    checkouts = fetch_abandoned_checkouts(connection, limit=limit)
    queued = 0
    skipped = 0
    for checkout in checkouts:
        payload = _abandoned_checkout_payload(checkout)
        if not payload.get("phone"):
            skipped += 1
            continue
        create_abandoned_cart_event(db, payload=payload, source="ecommerce_api" if force else "shopify_checkouts_api")
        queued += 1
    return {"status": "success", "fetched": len(checkouts), "queued": queued, "skipped": skipped}

def sync_customers(db: Session, connection: EcommerceConnection, limit: int = 100) -> dict:
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Customers are read directly from the ecommerce API and cached temporarily when needed; they are not stored in Neon.",
        "connection_id": connection.id,
    }

def _abandoned_checkout_payload(checkout: dict) -> dict:
    customer = checkout.get("customer") or {}
    shipping = checkout.get("shipping_address") or {}
    billing = checkout.get("billing_address") or {}
    first = shipping.get("first_name") or billing.get("first_name") or customer.get("first_name") or ""
    last = shipping.get("last_name") or billing.get("last_name") or customer.get("last_name") or ""
    return {
        "external_id": str(checkout.get("id") or checkout.get("token") or ""),
        "phone": checkout.get("phone")
        or shipping.get("phone")
        or billing.get("phone")
        or customer.get("phone"),
        "customer_name": " ".join([first, last]).strip() or customer.get("email") or "there",
        "cart_url": checkout.get("abandoned_checkout_url") or checkout.get("cart_url") or checkout.get("web_url") or "",
        "total": str(checkout.get("total_price") or checkout.get("total") or ""),
        "currency": checkout.get("currency") or "",
        "items": checkout.get("line_items") or checkout.get("items") or [],
    }

def _int_value(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0

def register_shopify_webhooks(db: Session, connection: EcommerceConnection) -> dict:
    if connection.platform != "shopify":
        return {"status": "skipped", "reason": "not_shopify"}
    base_url = settings.PUBLIC_WEBHOOK_BASE_URL
    if not base_url:
        connection.webhook_status = "missing_public_url"
        db.commit()
        return {"status": "skipped", "reason": "PUBLIC_WEBHOOK_BASE_URL or APP_URL is required"}

    registered = 0
    failed = []
    for topic, path in SHOPIFY_WEBHOOK_TOPICS.items():
        callback_url = f"{base_url}{path}"
        payload = {
            "webhook": {
                "topic": topic,
                "address": callback_url,
                "format": "json",
            }
        }
        try:
            _shopify_request("POST", connection, "/webhooks.json", payload=payload)
            registered += 1
        except requests.HTTPError as exc:
            response_text = getattr(exc.response, "text", "")
            if exc.response is not None and exc.response.status_code in {400, 422} and "address" in response_text:
                registered += 1
                continue
            failed.append({"topic": topic, "error": str(exc)})
        except requests.RequestException as exc:
            failed.append({"topic": topic, "error": str(exc)})

    connection.webhook_status = "active" if not failed else "partial"
    db.add(
        AgentAction(
            action_type="shopify_webhook_registration",
            status=connection.webhook_status,
            payload=_json_dumps({"connection_id": connection.id, "topics": list(SHOPIFY_WEBHOOK_TOPICS)}),
            result=_json_dumps({"registered": registered, "failed": failed}),
        )
    )
    db.commit()
    return {"status": connection.webhook_status, "registered": registered, "failed": failed}

def bootstrap_shopify_connection(db: Session, connection: EcommerceConnection) -> dict:
    result = {"connection_id": connection.id}
    try:
        result["test"] = test_connection(connection)
        db.commit()
        db.refresh(connection)
        if connection.platform == "shopify" and result["test"].get("scopes", {}).get("missing"):
            connection.status = "missing_scopes"
            db.add(
                AgentAction(
                    action_type="shopify_connection_missing_scopes",
                    status="failed",
                    payload=_json_dumps({"connection_id": connection.id, "store_url": connection.store_url}),
                    result=_json_dumps(result["test"]["scopes"]),
                )
            )
            db.commit()
            return result
    except Exception as exc:
        connection.status = "failed"
        db.add(
            AgentAction(
                action_type="shopify_connection_bootstrap_failed",
                status="failed",
                payload=_json_dumps({"connection_id": connection.id, "store_url": connection.store_url}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()
        raise

    result["live_api_mode"] = {
        "status": "enabled",
        "message": "Shopify product, order, and customer data are read live and cached in Redis.",
    }

    try:
        result["webhooks"] = register_shopify_webhooks(db, connection)
    except Exception as exc:
        result["webhooks"] = {"status": "skipped", "error": str(exc)}
        connection.webhook_status = "failed"
        db.add(
            AgentAction(
                action_type="shopify_bootstrap_webhooks_skipped",
                status="skipped",
                payload=_json_dumps({"connection_id": connection.id}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()

    connection.status = "active"
    db.commit()
    return result
