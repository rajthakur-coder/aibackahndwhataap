import json

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.models.entities import AgentAction, EcommerceConnection, EcommerceOrder, EcommerceProduct
from app.schemas import (
    AbandonedCartRequest,
    DeliveredFollowupRequest,
    EcommerceConnectionRequest,
    EcommerceConnectionUpdateRequest,
    EcommerceProductSyncRequest,
    EcommerceSyncRequest,
)
from app.services.automations import (
    create_abandoned_cart_event,
    enqueue_order_automation_events,
    process_automation_event,
    serialize_event,
)
from app.services.ecommerce import (
    create_connection,
    send_delivered_followups,
    sync_orders,
    sync_products,
    test_connection,
    update_connection,
    upsert_order as upsert_ecommerce_order,
)
from app.services.ecommerce_sync import sync_active_ecommerce_connections, sync_product_catalog_knowledge
from app.services.serializers import (
    serialize_ecommerce_connection,
    serialize_ecommerce_order,
    serialize_ecommerce_product,
)


router = APIRouter(prefix="/ecommerce", tags=["ecommerce"])


@router.post("/connections")
def add_ecommerce_connection(
    data: EcommerceConnectionRequest,
    db: Session = Depends(get_db),
):
    try:
        connection = create_connection(
            db,
            name=data.name,
            platform=data.platform,
            store_url=data.store_url,
            access_token=data.access_token,
            consumer_key=data.consumer_key,
            consumer_secret=data.consumer_secret,
        )
        return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/connections")
def list_ecommerce_connections(db: Session = Depends(get_db)):
    rows = db.query(EcommerceConnection).order_by(EcommerceConnection.created_at.desc()).all()
    return [serialize_ecommerce_connection(row) for row in rows]


@router.patch("/connections/{connection_id}")
def patch_ecommerce_connection(
    connection_id: int,
    data: EcommerceConnectionUpdateRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        connection = update_connection(
            db,
            connection,
            name=data.name,
            store_url=data.store_url,
            access_token=data.access_token,
            consumer_key=data.consumer_key,
            consumer_secret=data.consumer_secret,
            status=data.status,
        )
        return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/test")
async def check_ecommerce_connection(connection_id: int, db: Session = Depends(get_db)):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        return await run_in_threadpool(test_connection, connection)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/sync-orders")
async def sync_ecommerce_orders(
    connection_id: int,
    data: EcommerceSyncRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        return await run_in_threadpool(sync_orders, db, connection, data.limit)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/connections/{connection_id}/sync-products")
async def sync_ecommerce_products(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        result = await run_in_threadpool(sync_products, db, connection, data.limit)
        result.update(sync_product_catalog_knowledge(db, connection, data.limit))
        return result
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/products")
def list_ecommerce_products(
    connection_id: int | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(EcommerceProduct)
    if connection_id is not None:
        query = query.filter(EcommerceProduct.connection_id == connection_id)
    if q:
        search = f"%{q.strip()}%"
        query = query.filter(
            (EcommerceProduct.title.ilike(search))
            | (EcommerceProduct.description.ilike(search))
            | (EcommerceProduct.tags.ilike(search))
            | (EcommerceProduct.sku.ilike(search))
        )

    rows = query.order_by(EcommerceProduct.updated_at.desc()).limit(200).all()
    return [serialize_ecommerce_product(row) for row in rows]


@router.post("/sync-active")
async def sync_all_active_ecommerce_connections():
    return await run_in_threadpool(sync_active_ecommerce_connections)


@router.post("/connections/{connection_id}/webhook/order")
async def receive_ecommerce_order_webhook(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid ecommerce webhook JSON") from exc

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


@router.post("/connections/{connection_id}/webhook/abandoned-cart")
async def receive_abandoned_cart_webhook(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    connection = db.query(EcommerceConnection).filter(EcommerceConnection.id == connection_id).first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")

    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid abandoned cart webhook JSON") from exc

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
    payload = {
        "external_id": str(checkout.get("id") or checkout.get("token") or ""),
        "phone": phone,
        "customer_name": customer_name or customer.get("name") or "there",
        "cart_url": checkout.get("abandoned_checkout_url") or checkout.get("cart_url") or checkout.get("web_url") or "",
        "total": str(checkout.get("total_price") or checkout.get("total") or ""),
        "currency": checkout.get("currency") or "",
        "items": checkout.get("line_items") or checkout.get("items") or [],
    }
    if not payload["phone"]:
        raise HTTPException(status_code=400, detail="Abandoned cart phone is required")

    event = create_abandoned_cart_event(db, payload=payload, source="ecommerce_abandoned_cart_webhook")
    result = process_automation_event(db, event)
    return {"status": "queued", "event": serialize_event(event), "automation": result}


@router.post("/abandoned-cart")
def add_abandoned_cart(
    data: AbandonedCartRequest,
    db: Session = Depends(get_db),
):
    event = create_abandoned_cart_event(
        db,
        payload=data.model_dump(),
        source="ecommerce_api",
        delay_seconds=data.delay_seconds,
    )
    result = process_automation_event(db, event)
    return {"status": "queued", "event": serialize_event(event), "automation": result}


@router.get("/orders")
def list_ecommerce_orders(
    platform: str | None = None,
    phone: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(EcommerceOrder)
    if platform:
        query = query.filter(EcommerceOrder.platform == platform.strip().lower())
    if phone:
        query = query.filter(EcommerceOrder.phone == phone)
    if status:
        query = query.filter(EcommerceOrder.status == status)

    rows = query.order_by(EcommerceOrder.updated_at.desc()).limit(200).all()
    return [serialize_ecommerce_order(row) for row in rows]


@router.get("/orders/{order_id}")
def get_ecommerce_order(order_id: str, db: Session = Depends(get_db)):
    normalized_order_id = order_id.strip().lstrip("#")
    row = (
        db.query(EcommerceOrder)
        .filter(
            (EcommerceOrder.order_number == order_id)
            | (EcommerceOrder.order_number == f"#{normalized_order_id}")
            | (EcommerceOrder.external_id == normalized_order_id)
        )
        .order_by(EcommerceOrder.updated_at.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Ecommerce order not found")
    return serialize_ecommerce_order(row)


@router.post("/automations/delivered-followups")
async def run_delivered_followups(
    data: DeliveredFollowupRequest,
    db: Session = Depends(get_db),
):
    return await run_in_threadpool(send_delivered_followups, db, data.limit)
