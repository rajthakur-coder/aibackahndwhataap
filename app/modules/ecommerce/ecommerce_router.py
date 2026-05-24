import json

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal, get_db
from app.models.crm import AgentAction
from app.models.ecommerce import ContactStoreMapping, EcommerceConnection, ShopifyCatalogCollection
from app.modules.automation.automation_service import (
    enqueue_order_automation_events,
    process_automation_event,
    serialize_event,
)
from app.modules.automation.core.automation_sync_service import (
    create_abandoned_cart_event as create_sync_abandoned_cart_event,
)
from app.modules.ecommerce.core.ecommerce_serializers import (
    serialize_ecommerce_connection,
    serialize_ecommerce_order,
)
from app.modules.ecommerce.ecommerce_schema import (
    AbandonedCartRequest,
    DeliveredFollowupRequest,
    EcommerceConnectionRequest,
    EcommerceConnectionUpdateRequest,
    EcommerceProductSyncRequest,
    EcommerceSyncRequest,
    ShopifyCatalogCollectionUpdateRequest,
)
from app.modules.ecommerce.ecommerce_service import (
    create_connection,
    fetch_fulfillments,
    fetch_order_by_id,
    fetch_locations,
    fetch_shopify_collections,
    fetch_shopify_collects,
    find_shopify_connection_by_domain,
    mark_shopify_webhook_event,
    record_shopify_webhook_event,
    send_delivered_followups,
    sync_abandoned_checkouts,
    sync_active_ecommerce_connections,
    test_connection,
    update_connection,
    upsert_order as upsert_ecommerce_order,
    validate_shopify_scopes,
    verify_shopify_hmac,
)
from app.modules.ecommerce.core.ecommerce_core_service import (
    bootstrap_shopify_connection,
    upsert_contact_store_mapping,
)
from app.shared.redis import get_redis


ecommerce_router = APIRouter(prefix="/ecommerce", tags=["ecommerce"])
shopify_webhooks_router = APIRouter(prefix="/webhooks/shopify", tags=["shopify-webhooks"])


def _connection_or_404(db: Session, connection_id: int) -> EcommerceConnection:
    connection = db.execute(
        select(EcommerceConnection).where(EcommerceConnection.id == connection_id)
    ).scalars().first()
    if not connection:
        raise HTTPException(status_code=404, detail="Ecommerce connection not found")
    return connection


def _db_bool(value: bool) -> str:
    return "true" if value else "false"


def _is_db_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


async def _clear_shopify_catalog_cache(connection_id: int) -> None:
    try:
        redis = await get_redis()
        patterns = [
            f"shopify:collections:*:{connection_id}",
            f"shopify:collection-index:*:{connection_id}",
            f"shopify:products:all:*:{connection_id}",
            f"shopify:top-selling:*:{connection_id}:*",
            f"shopify:fbt:*:{connection_id}",
            f"shopify:categories:*:{connection_id}:*",
            f"shopify:category:*:{connection_id}:*",
            f"shopify:query:*:{connection_id}:*",
            f"shopify:cross-sell:*:{connection_id}:*",
        ]
        for pattern in patterns:
            batch = []
            async for key in redis.scan_iter(match=pattern, count=100):
                batch.append(key)
                if len(batch) >= 100:
                    await redis.delete(*batch)
                    batch = []
            if batch:
                await redis.delete(*batch)
    except Exception:
        return


def _shopify_collection_rows(connection: EcommerceConnection) -> list[dict]:
    collections = fetch_shopify_collections(connection, 250)
    collects = fetch_shopify_collects(connection, 5000)
    counts: dict[str, int] = {}
    for collect in collects:
        collection_id = str(collect.get("collection_id") or "")
        if collection_id:
            counts[collection_id] = counts.get(collection_id, 0) + 1
    rows = []
    for collection in collections:
        collection_id = str(collection.get("id") or "")
        if not collection_id:
            continue
        rows.append(
            {
                "shopify_collection_id": collection_id,
                "title": collection.get("title") or collection.get("handle") or collection_id,
                "handle": collection.get("handle"),
                "product_count": counts.get(collection_id, 0),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["product_count"] or 0), str(row["title"]).lower()))


def _serialize_contact_store_mapping(row: ContactStoreMapping) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "phone": row.phone,
        "normalized_phone": row.normalized_phone,
        "connection_id": row.connection_id,
        "source": row.source,
        "status": row.status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


async def bootstrap_shopify_connection_background(connection_id: int) -> None:
    async with AsyncSessionLocal() as db:
        def sync_op(sync_db: Session):
            connection = _connection_or_404(sync_db, connection_id)
            connection.status = "syncing"
            sync_db.commit()
            bootstrap_shopify_connection(sync_db, connection)

        await db.run_sync(sync_op)


@ecommerce_router.post("/connections")
async def add_ecommerce_connection(
    data: EcommerceConnectionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        try:
            connection = create_connection(
                sync_db,
                name=data.name,
                platform=data.platform,
                store_url=data.store_url,
                access_token=data.access_token,
                consumer_key=data.consumer_key,
                consumer_secret=data.consumer_secret,
                run_bootstrap=data.platform.strip().lower() != "shopify",
            )
            return {"status": "queued" if connection.platform == "shopify" else "success", "connection": serialize_ecommerce_connection(connection)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    response = await db.run_sync(sync_op)
    connection = response.get("connection") or {}
    if connection.get("platform") == "shopify" and connection.get("id"):
        background_tasks.add_task(bootstrap_shopify_connection_background, connection["id"])
    return response


@ecommerce_router.get("/connections")
async def list_ecommerce_connections(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EcommerceConnection).order_by(EcommerceConnection.created_at.desc()))
    rows = result.scalars().all()
    return [serialize_ecommerce_connection(row) for row in rows]


@ecommerce_router.patch("/connections/{connection_id}")
async def patch_ecommerce_connection(
    connection_id: int,
    data: EcommerceConnectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        try:
            connection = update_connection(
                sync_db,
                connection,
                name=data.name,
                store_url=data.store_url,
                access_token=data.access_token,
                consumer_key=data.consumer_key,
                consumer_secret=data.consumer_secret,
                status=data.status,
            )
            if data.bot_enabled is not None:
                connection.bot_enabled = _db_bool(data.bot_enabled)
                sync_db.commit()
                sync_db.refresh(connection)
            return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/test")
async def check_ecommerce_connection(connection_id: int, db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        try:
            return test_connection(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.get("/connections/{connection_id}/shopify-scopes")
@ecommerce_router.post("/connections/{connection_id}/verify-scopes")
async def verify_ecommerce_shopify_scopes(connection_id: int, db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        if connection.platform != "shopify":
            raise HTTPException(status_code=400, detail="Scope verification is only available for Shopify")
        try:
            return validate_shopify_scopes(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/sync-orders")
async def sync_ecommerce_orders(
    connection_id: int,
    data: EcommerceSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Orders are read directly from Shopify API and cached in Redis.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/sync-products")
async def sync_ecommerce_products(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Products are read directly from Shopify API and cached in Redis.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/sync-inventory")
async def sync_ecommerce_inventory(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Inventory is read directly from Shopify API when product data is requested.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/sync-checkouts")
async def sync_ecommerce_checkouts(
    connection_id: int,
    data: EcommerceSyncRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        try:
            return sync_abandoned_checkouts(sync_db, connection, data.limit)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.get("/connections/{connection_id}/locations")
async def get_ecommerce_locations(connection_id: int, db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        try:
            return {"status": "success", "locations": fetch_locations(connection)}
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.get("/connections/{connection_id}/contact-store-mappings")
async def list_contact_store_mappings(
    connection_id: int,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        rows = sync_db.execute(
            select(ContactStoreMapping)
            .where(
                ContactStoreMapping.tenant_id == connection.tenant_id,
                ContactStoreMapping.connection_id == connection.id,
            )
            .order_by(ContactStoreMapping.last_seen_at.desc())
            .limit(200)
        ).scalars().all()
        return {
            "status": "success",
            "connection_id": connection.id,
            "mappings": [_serialize_contact_store_mapping(row) for row in rows],
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/contact-store-mappings")
async def save_contact_store_mapping(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()

    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        mapping = upsert_contact_store_mapping(
            sync_db,
            connection,
            str(body.get("phone") or body.get("customer_phone_number") or ""),
            source=str(body.get("source") or "manual"),
        )
        if not mapping:
            raise HTTPException(status_code=400, detail="phone is required")
        sync_db.commit()
        sync_db.refresh(mapping)
        return {
            "status": "success",
            "connection_id": connection.id,
            "mapping": _serialize_contact_store_mapping(mapping),
        }

    return await db.run_sync(sync_op)


@ecommerce_router.get("/connections/{connection_id}/shopify-collections")
async def get_shopify_catalog_collections(connection_id: int, db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        if connection.platform != "shopify":
            raise HTTPException(status_code=400, detail="Collections are only available for Shopify")

        saved_rows = sync_db.execute(
            select(ShopifyCatalogCollection).where(
                ShopifyCatalogCollection.connection_id == connection.id
            )
        ).scalars().all()
        saved_by_id = {str(row.shopify_collection_id): row for row in saved_rows}

        try:
            collections = _shopify_collection_rows(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        has_saved_preferences = bool(saved_rows)
        data = []
        for index, collection in enumerate(collections):
            saved = saved_by_id.get(collection["shopify_collection_id"])
            visible = _is_db_true(saved.visible) if saved else not has_saved_preferences
            data.append(
                {
                    **collection,
                    "visible": visible,
                    "sort_order": saved.sort_order if saved else index,
                }
            )
        return {"status": "success", "connection_id": connection.id, "collections": data}

    return await db.run_sync(sync_op)


@ecommerce_router.put("/connections/{connection_id}/shopify-collections")
async def update_shopify_catalog_collections(
    connection_id: int,
    data: ShopifyCatalogCollectionUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        if connection.platform != "shopify":
            raise HTTPException(status_code=400, detail="Collections are only available for Shopify")

        try:
            live_collections = _shopify_collection_rows(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        live_by_id = {row["shopify_collection_id"]: row for row in live_collections}
        selected_by_id = {
            str(row.shopify_collection_id): row
            for row in data.collections
            if str(row.shopify_collection_id) in live_by_id
        }

        sync_db.execute(
            delete(ShopifyCatalogCollection).where(
                ShopifyCatalogCollection.connection_id == connection.id
            )
        )
        for fallback_index, (collection_id, selection) in enumerate(selected_by_id.items()):
            live = live_by_id[collection_id]
            sync_db.add(
                ShopifyCatalogCollection(
                    tenant_id=connection.tenant_id,
                    connection_id=connection.id,
                    shopify_collection_id=collection_id,
                    title=live["title"],
                    handle=live.get("handle"),
                    product_count=live.get("product_count") or 0,
                    visible=_db_bool(selection.visible),
                    sort_order=selection.sort_order if selection.sort_order is not None else fallback_index,
                )
            )
        sync_db.commit()
        return {
            "status": "success",
            "connection_id": connection.id,
            "saved": len(selected_by_id),
        }

    response = await db.run_sync(sync_op)
    await _clear_shopify_catalog_cache(connection_id)
    return response


@ecommerce_router.get("/connections/{connection_id}/orders/{shopify_order_id}/fulfillments")
async def get_ecommerce_order_fulfillments(
    connection_id: int,
    shopify_order_id: str,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        try:
            return {
                "status": "success",
                "fulfillments": fetch_fulfillments(connection, shopify_order_id),
            }
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@ecommerce_router.get("/products")
async def list_ecommerce_products(
    connection_id: int | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Products are read directly from the ecommerce API and cached in Redis; Neon is not used as the product catalog.",
        "data": [],
    }


@ecommerce_router.post("/sync-active")
async def sync_all_active_ecommerce_connections():
    return await sync_active_ecommerce_connections()


def _shopify_webhook_context_sync(
    db: Session,
    raw_body: bytes,
    headers,
) -> tuple[EcommerceConnection, dict, object]:
    if not verify_shopify_hmac(raw_body, headers.get("X-Shopify-Hmac-Sha256")):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature")

    shop_domain = headers.get("X-Shopify-Shop-Domain")
    topic = headers.get("X-Shopify-Topic") or "unknown"
    if not shop_domain:
        raise HTTPException(status_code=400, detail="X-Shopify-Shop-Domain header is required")

    connection = find_shopify_connection_by_domain(db, shop_domain)
    if not connection:
        raise HTTPException(status_code=404, detail="Shopify ecommerce connection not found")

    event, already_processed = record_shopify_webhook_event(
        db,
        connection,
        shop_domain,
        topic,
        headers.get("X-Shopify-Webhook-Id"),
        raw_body,
    )
    if already_processed:
        return connection, {"_duplicate": True}, event

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        mark_shopify_webhook_event(db, event, "failed", str(exc))
        raise HTTPException(status_code=400, detail="Invalid Shopify webhook JSON") from exc
    return connection, body, event


@shopify_webhooks_router.post("/orders")
async def shopify_orders_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers

    def sync_op(sync_db: Session):
        connection, body, event = _shopify_webhook_context_sync(sync_db, raw_body, headers)
        if body.get("_duplicate"):
            return {"status": "ignored", "reason": "duplicate"}

        order_payload = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
        try:
            order = upsert_ecommerce_order(sync_db, connection, order_payload)
        except Exception as exc:
            sync_db.add(
                AgentAction(
                    action_type="shopify_order_webhook_failed",
                    status="failed",
                    payload=json.dumps({"shop_domain": connection.store_url, "connection_id": connection.id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            sync_db.commit()
            mark_shopify_webhook_event(sync_db, event, "failed", str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        automation_results = []
        try:
            events = enqueue_order_automation_events(sync_db, order, source="shopify_webhook")
            automation_results = [process_automation_event(sync_db, event_row) for event_row in events]
        except Exception as exc:
            sync_db.add(
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
            sync_db.commit()

        mark_shopify_webhook_event(sync_db, event, "processed")
        return {
            "status": "success",
            "order": serialize_ecommerce_order(order),
            "automations": automation_results,
        }

    return await db.run_sync(sync_op)


@shopify_webhooks_router.post("/products")
async def shopify_products_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers

    def sync_op(sync_db: Session):
        connection, body, event = _shopify_webhook_context_sync(sync_db, raw_body, headers)
        if body.get("_duplicate"):
            return {"status": "ignored", "reason": "duplicate"}
        product_payload = body.get("product") if isinstance(body, dict) and isinstance(body.get("product"), dict) else body
        try:
            mark_shopify_webhook_event(sync_db, event, "processed")
            return {
                "status": "ignored",
                "reason": "live_api_mode",
                "external_id": product_payload.get("id") if isinstance(product_payload, dict) else None,
            }
        except Exception as exc:
            mark_shopify_webhook_event(sync_db, event, "failed", str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@shopify_webhooks_router.post("/fulfillments")
async def shopify_fulfillments_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers

    def sync_op(sync_db: Session):
        connection, body, event = _shopify_webhook_context_sync(sync_db, raw_body, headers)
        if body.get("_duplicate"):
            return {"status": "ignored", "reason": "duplicate"}
        order_id = str(body.get("order_id") or "")
        if not order_id:
            mark_shopify_webhook_event(sync_db, event, "processed")
            return {"status": "accepted", "reason": "missing_order_id"}

        automation_results = []
        try:
            order_payload = fetch_order_by_id(connection, order_id)
            if order_payload:
                order = upsert_ecommerce_order(sync_db, connection, order_payload)
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
                    events = enqueue_order_automation_events(sync_db, order, source="shopify_fulfillment_webhook", triggers=triggers)
                    automation_results = [process_automation_event(sync_db, event_row) for event_row in events]
        except Exception as exc:
            sync_db.add(
                AgentAction(
                    action_type="shopify_fulfillment_automation_failed",
                    status="failed",
                    payload=json.dumps({"shop_domain": connection.store_url, "connection_id": connection.id, "order_id": order_id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            sync_db.commit()
            mark_shopify_webhook_event(sync_db, event, "failed", str(exc))
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        mark_shopify_webhook_event(sync_db, event, "processed")
        return {
            "status": "success",
            "reason": "live_api_mode",
            "message": "Fulfillment webhook processed without storing Shopify order data in Neon.",
            "connection_id": connection.id,
            "automations": automation_results,
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/webhook/order")
async def receive_ecommerce_order_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid ecommerce webhook JSON") from exc

    def sync_op(sync_db: Session):
        connection = _connection_or_404(sync_db, connection_id)
        order_payload = body.get("order") if isinstance(body, dict) and isinstance(body.get("order"), dict) else body
        try:
            order = upsert_ecommerce_order(sync_db, connection, order_payload)
        except Exception as exc:
            sync_db.add(
                AgentAction(
                    action_type="ecommerce_order_webhook_failed",
                    status="failed",
                    payload=json.dumps({"connection_id": connection.id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            sync_db.commit()
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        automation_results = []
        try:
            events = enqueue_order_automation_events(sync_db, order, source="ecommerce_order_webhook")
            automation_results = [process_automation_event(sync_db, event) for event in events]
        except Exception as exc:
            sync_db.add(
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
            sync_db.commit()

        return {
            "status": "success",
            "order": serialize_ecommerce_order(order),
            "automations": automation_results,
        }

    return await db.run_sync(sync_op)


@ecommerce_router.post("/connections/{connection_id}/webhook/abandoned-cart")
async def receive_abandoned_cart_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid abandoned cart webhook JSON") from exc

    def sync_op(sync_db: Session):
        _connection_or_404(sync_db, connection_id)
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

        event = create_sync_abandoned_cart_event(sync_db, payload=payload, source="ecommerce_abandoned_cart_webhook")
        result = process_automation_event(sync_db, event)
        return {"status": "queued", "event": serialize_event(event), "automation": result}

    return await db.run_sync(sync_op)


@ecommerce_router.post("/abandoned-cart")
async def add_abandoned_cart(
    data: AbandonedCartRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        event = create_sync_abandoned_cart_event(
            sync_db,
            payload=data.model_dump(),
            source="ecommerce_api",
            delay_seconds=data.delay_seconds,
        )
        result = process_automation_event(sync_db, event)
        return {"status": "queued", "event": serialize_event(event), "automation": result}

    return await db.run_sync(sync_op)


@ecommerce_router.get("/orders")
async def list_ecommerce_orders(
    platform: str | None = None,
    phone: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Orders are read directly from the ecommerce API and cached in Redis; Neon is not used as the order store.",
        "data": [],
    }


@ecommerce_router.get("/orders/{order_id}")
async def get_ecommerce_order(order_id: str, db: AsyncSession = Depends(get_db)):
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Order details are read directly from the ecommerce API when requested; Neon is not used as the order store.",
        "order_id": order_id,
        "data": None,
    }


@ecommerce_router.get("/customers")
async def list_ecommerce_customers(
    connection_id: int | None = None,
    phone: str | None = None,
    email: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Customers are read directly from the ecommerce API and cached temporarily when needed; Neon is not used as the customer store.",
        "data": [],
    }


@ecommerce_router.post("/automations/delivered-followups")
async def run_delivered_followups(
    data: DeliveredFollowupRequest,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: send_delivered_followups(sync_db, data.limit))
