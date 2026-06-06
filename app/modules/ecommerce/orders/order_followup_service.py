import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import AgentAction
from app.models.ecommerce import (
    ContactStoreMapping,
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
)

DELIVERED_STATUSES = {"delivered"}


from app.modules.ecommerce.orders.order_normalizer_service import *

def order_status_text(order: EcommerceOrder) -> str:
    status = order.delivery_status or order.shipment_status or order.fulfillment_status or order.status
    if status:
        parts = [f"Your order {order.order_number} status is {status}."]
    elif order.financial_status:
        parts = [
            f"Your order {order.order_number} payment status is {order.financial_status}. "
            "Fulfillment status is not available yet."
        ]
    else:
        parts = [f"I could not confirm the latest status for order {order.order_number} right now."]
    if order.tracking_number:
        parts.append(f"Tracking number: {order.tracking_number}.")
    if order.tracking_url:
        parts.append(f"Track here: {order.tracking_url}")
    if order.total:
        parts.append(f"Total: {order.total} {order.currency or ''}".strip())
    return " ".join(parts)

def cross_sell_text(order: EcommerceOrder) -> str:
    items = []
    if order.items:
        try:
            items = [item.get("name") for item in json.loads(order.items) if item.get("name")]
        except json.JSONDecodeError:
            items = []

    if items:
        return (
            f"Thanks for shopping with us. Since you ordered {items[0]}, "
            "you may also like our matching accessories or next best-seller. Reply YES and our team will share options."
        )
    return "Thanks for shopping with us. Reply YES if you want our best new offers and matching product suggestions."

def _raw_payload(order: EcommerceOrder) -> dict:
    if not order.raw_payload:
        return {}
    try:
        data = json.loads(order.raw_payload)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

def _status_values(order: EcommerceOrder) -> set[str]:
    values = {
        order.status,
        order.fulfillment_status,
    }
    payload = _raw_payload(order)
    values.update(
        [
            payload.get("status"),
            payload.get("delivery_status"),
            payload.get("shipment_status"),
        ]
    )

    for fulfillment in payload.get("fulfillments") or []:
        if isinstance(fulfillment, dict):
            values.update(
                [
                    fulfillment.get("status"),
                    fulfillment.get("shipment_status"),
                    fulfillment.get("delivery_status"),
                ]
            )

    meta_data = payload.get("meta_data") or []
    for item in meta_data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").lower()
        if "deliver" in key or "shipment" in key or "tracking" in key:
            values.add(item.get("value"))

    return {str(value).strip().lower() for value in values if value}

def is_delivered_order(order: EcommerceOrder) -> bool:
    return bool(_status_values(order) & DELIVERED_STATUSES)

def send_delivered_followups(db: Session, limit: int = 25) -> dict:
    from app.modules.automation.automation_service import (
        TRIGGER_ORDER_DELIVERED,
        enqueue_order_automation_events,
        process_automation_event,
    )

    orders = db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.delivered_message_sent_at.is_(None))
        .order_by(EcommerceOrder.updated_at.desc())
        .limit(limit)
    ).scalars().all()

    sent = 0
    skipped = 0
    for order in orders:
        if not is_delivered_order(order) or not order.phone:
            skipped += 1
            continue

        try:
            events = enqueue_order_automation_events(
                db,
                order,
                source="delivered_followup",
                triggers=[TRIGGER_ORDER_DELIVERED],
            )
            results = [process_automation_event(db, event) for event in events]
            was_sent = any(result.get("sent", 0) > 0 for result in results)
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=order.phone,
                    action_type="delivered_followup_failed",
                    status="failed",
                    payload=json.dumps({"order_id": order.order_number}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            skipped += 1
            continue

        if not was_sent:
            skipped += 1
            continue

        order.delivered_message_sent_at = datetime.utcnow()
        db.add(
            AgentAction(
                phone=order.phone,
                action_type="delivered_followup_sent",
                status="sent",
                payload=json.dumps({"order_id": order.order_number}),
                result=json.dumps({"processor": "automation_engine"}),
            )
        )
        db.commit()
        sent += 1

    return {"status": "success", "sent": sent, "skipped": skipped}

__all__ = [
    "order_status_text",
    "cross_sell_text",
    "_raw_payload",
    "_status_values",
    "is_delivered_order",
    "send_delivered_followups",
]
