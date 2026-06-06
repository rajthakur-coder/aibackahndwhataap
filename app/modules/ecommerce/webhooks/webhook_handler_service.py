import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.crm import AgentAction
from app.modules.automation.automation_service import (
    enqueue_order_automation_events,
    process_automation_event,
    serialize_event,
)
from app.modules.automation.runtime.sync_service import (
    create_abandoned_cart_event as create_sync_abandoned_cart_event,
)
from app.modules.ecommerce.shared.router_service import (
    connection_or_404,
    shopify_webhook_context,
)
from app.modules.ecommerce.shared.serializers import serialize_ecommerce_order
from app.modules.ecommerce.ecommerce_service import (
    fetch_order_by_id,
    mark_shopify_webhook_event,
    upsert_order as upsert_ecommerce_order,
)


def handle_shopify_orders_webhook(db: Session, raw_body: bytes, headers, request_id: str | None = None) -> dict:
    connection, body, event = shopify_webhook_context(db, raw_body, headers, request_id=request_id)
    if body.get("_duplicate"):
        return {"status": "ignored", "reason": "duplicate"}

    order_payload = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
    try:
        order = upsert_ecommerce_order(db, connection, order_payload)
    except Exception as exc:
        db.add(
            AgentAction(
                action_type="shopify_order_webhook_failed",
                status="failed",
                payload=json.dumps({"shop_domain": connection.store_url, "connection_id": connection.id}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()
        mark_shopify_webhook_event(db, event, "failed", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    automation_results = []
    try:
        events = enqueue_order_automation_events(db, order, source="shopify_webhook")
        automation_results = [process_automation_event(db, event_row) for event_row in events]
    except Exception as exc:
        db.add(
            AgentAction(
                phone=order.phone,
                action_type="shopify_order_automation_failed",
                status="failed",
                payload=json.dumps(
                    {
                        "shop_domain": connection.store_url,
                        "connection_id": connection.id,
                        "order_id": order.order_number,
                    }
                ),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()

    mark_shopify_webhook_event(db, event, "processed")
    return {
        "status": "success",
        "order": serialize_ecommerce_order(order),
        "automations": automation_results,
    }


def handle_shopify_products_webhook(db: Session, raw_body: bytes, headers, request_id: str | None = None) -> dict:
    connection, body, event = shopify_webhook_context(db, raw_body, headers, request_id=request_id)
    if body.get("_duplicate"):
        return {"status": "ignored", "reason": "duplicate"}
    product_payload = body.get("product") if isinstance(body, dict) and isinstance(body.get("product"), dict) else body
    try:
        mark_shopify_webhook_event(db, event, "processed")
        return {
            "status": "ignored",
            "reason": "live_api_mode",
            "external_id": product_payload.get("id") if isinstance(product_payload, dict) else None,
        }
    except Exception as exc:
        mark_shopify_webhook_event(db, event, "failed", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def handle_shopify_fulfillments_webhook(db: Session, raw_body: bytes, headers, request_id: str | None = None) -> dict:
    connection, body, event = shopify_webhook_context(db, raw_body, headers, request_id=request_id)
    if body.get("_duplicate"):
        return {"status": "ignored", "reason": "duplicate"}
    order_id = str(body.get("order_id") or "")
    if not order_id:
        mark_shopify_webhook_event(db, event, "processed")
        return {"status": "accepted", "reason": "missing_order_id"}

    automation_results = []
    try:
        order_payload = fetch_order_by_id(connection, order_id)
        if order_payload:
            order = upsert_ecommerce_order(db, connection, order_payload)
            status_values = {
                str(order.fulfillment_status or "").lower(),
                str(order.shipment_status or "").lower(),
                str(order.delivery_status or "").lower(),
            }
            fulfillment_status = str(body.get("status") or body.get("shipment_status") or "").lower()
            if fulfillment_status:
                status_values.add(fulfillment_status)

            triggers = []
            if status_values & {"delivered"}:
                triggers = ["order_delivered", "feedback_request"]
            elif status_values & {"fulfilled", "shipped", "success", "closed"} or order.tracking_number or order.tracking_url:
                triggers = ["order_shipped"]

            if triggers:
                events = enqueue_order_automation_events(
                    db,
                    order,
                    source="shopify_fulfillment_webhook",
                    triggers=triggers,
                )
                automation_results = [process_automation_event(db, event_row) for event_row in events]
    except Exception as exc:
        db.add(
            AgentAction(
                action_type="shopify_fulfillment_automation_failed",
                status="failed",
                payload=json.dumps({"shop_domain": connection.store_url, "connection_id": connection.id, "order_id": order_id}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()
        mark_shopify_webhook_event(db, event, "failed", str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    mark_shopify_webhook_event(db, event, "processed")
    return {
        "status": "success",
        "reason": "live_api_mode",
        "message": "Fulfillment webhook processed without storing Shopify order data in Neon.",
        "connection_id": connection.id,
        "automations": automation_results,
    }


def handle_ecommerce_order_webhook(db: Session, connection_id: int, body: dict) -> dict:
    connection = connection_or_404(db, connection_id)
    order_payload = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
    try:
        order = upsert_ecommerce_order(db, connection, order_payload)
    except Exception as exc:
        db.add(
            AgentAction(
                action_type="ecommerce_order_webhook_failed",
                status="failed",
                payload=json.dumps({"connection_id": connection.id}),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    automation_results = []
    try:
        events = enqueue_order_automation_events(db, order, source="ecommerce_order_webhook")
        automation_results = [process_automation_event(db, event) for event in events]
    except Exception as exc:
        db.add(
            AgentAction(
                phone=order.phone,
                action_type="ecommerce_order_automation_failed",
                status="failed",
                payload=json.dumps(
                    {
                        "connection_id": connection.id,
                        "order_id": order.order_number,
                    }
                ),
                result=json.dumps({"error": str(exc)}),
            )
        )
        db.commit()

    return {
        "status": "success",
        "order": serialize_ecommerce_order(order),
        "automations": automation_results,
    }


def handle_abandoned_cart_webhook(db: Session, connection_id: int, body: dict) -> dict:
    connection = connection_or_404(db, connection_id)
    checkout = body.get("checkout") if isinstance(body, dict) and isinstance(body.get("checkout"), dict) else body
    if not isinstance(checkout, dict):
        raise HTTPException(status_code=400, detail="Invalid abandoned cart payload")
    customer = checkout.get("customer") or {}
    shipping = checkout.get("shipping_address") or {}
    billing = checkout.get("billing_address") or {}
    phone = (
        checkout.get("phone")
        or shipping.get("phone")
        or billing.get("phone")
        or customer.get("phone")
    )
    customer_name = " ".join(
        value
        for value in [
            shipping.get("first_name") or customer.get("first_name"),
            shipping.get("last_name") or customer.get("last_name"),
        ]
        if value
    ).strip()
    items = checkout.get("line_items") or checkout.get("items") or []
    payload = {
        "tenant_id": connection.tenant_id,
        "external_id": str(checkout.get("id") or checkout.get("token") or ""),
        "phone": phone,
        "email": checkout.get("email") or customer.get("email"),
        "customer_name": customer_name or customer.get("name") or "there",
        "cart_url": checkout.get("abandoned_checkout_url") or checkout.get("cart_url") or checkout.get("web_url") or "",
        "total": str(checkout.get("total_price") or checkout.get("total") or ""),
        "currency": checkout.get("currency") or "",
        "items": items,
        "product_name": _first_item_name(items),
        "checkout_created_at": checkout.get("created_at"),
        "checkout_updated_at": checkout.get("updated_at"),
    }
    if not payload["phone"]:
        raise HTTPException(status_code=400, detail="Abandoned cart phone is required")

    event = create_sync_abandoned_cart_event(db, payload=payload, source="ecommerce_abandoned_cart_webhook")
    result = process_automation_event(db, event)
    return {"status": "queued", "event": serialize_event(event), "automation": result}


def _first_item_name(items: list[dict]) -> str:
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("presentment_title", "title", "name", "product_title", "sku"):
            value = str(item.get(key) or "").strip()
            if value:
                return value
    return ""


def queue_manual_abandoned_cart(db: Session, data) -> dict:
    event = create_sync_abandoned_cart_event(
        db,
        payload=data.model_dump(),
        source="ecommerce_api",
        delay_seconds=data.delay_seconds,
    )
    result = process_automation_event(db, event)
    return {"status": "queued", "event": serialize_event(event), "automation": result}
