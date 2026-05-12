import json

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.models.entities import AgentAction, EcommerceConnection, EcommerceOrder, EcommerceProduct
from app.schemas import (
    DeliveredFollowupRequest,
    EcommerceConnectionRequest,
    EcommerceConnectionUpdateRequest,
    EcommerceProductSyncRequest,
    EcommerceSyncRequest,
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

    return {"status": "success", "order": serialize_ecommerce_order(order)}


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
