import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ecommerce import ContactStoreMapping, EcommerceConnection, ShopifyWebhookEvent
from app.security import get_current_user_token
from app.modules.ecommerce.shared.serializers import (
    serialize_ecommerce_connection,
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
    fetch_locations,
    send_delivered_followups,
    sync_abandoned_checkouts,
    sync_active_ecommerce_connections,
    test_connection,
    update_connection,
    validate_shopify_scopes,
)
from app.modules.ecommerce.webhooks.webhook_handler_service import (
    handle_abandoned_cart_webhook,
    handle_ecommerce_order_webhook,
    handle_shopify_fulfillments_webhook,
    handle_shopify_orders_webhook,
    handle_shopify_products_webhook,
    queue_manual_abandoned_cart,
)
from app.modules.ecommerce.shared.router_service import (
    bootstrap_shopify_connection_background,
    clear_shopify_catalog_cache,
    connection_or_404,
    db_bool,
    serialize_contact_store_mapping,
    shopify_catalog_collections_payload,
    update_shopify_catalog_collections_payload,
    upsert_manual_contact_store_mapping,
)
from app.shared.tenant import strict_tenant_id

router = APIRouter()

@router.post("/connections")
async def add_ecommerce_connection(
    data: EcommerceConnectionRequest,
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(strict_tenant_id),
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
                tenant_id=tenant_id,
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

@router.get("/connections")
async def list_ecommerce_connections(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.tenant_id == tenant_id)
        .order_by(EcommerceConnection.created_at.desc())
    )
    rows = result.scalars().all()
    return [serialize_ecommerce_connection(row) for row in rows]

@router.patch("/connections/{connection_id}")
async def patch_ecommerce_connection(
    connection_id: int,
    data: EcommerceConnectionUpdateRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
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
                connection.bot_enabled = db_bool(data.bot_enabled)
                sync_db.commit()
                sync_db.refresh(connection)
            return {"status": "success", "connection": serialize_ecommerce_connection(connection)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/test")
async def check_ecommerce_connection(
    connection_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        try:
            return test_connection(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)

@router.get("/connections/{connection_id}/shopify-scopes")
@router.post("/connections/{connection_id}/verify-scopes")
async def verify_ecommerce_shopify_scopes(
    connection_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        if connection.platform != "shopify":
            raise HTTPException(status_code=400, detail="Scope verification is only available for Shopify")
        try:
            return validate_shopify_scopes(connection)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/sync-orders")
async def sync_ecommerce_orders(
    connection_id: int,
    data: EcommerceSyncRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Orders are read directly from Shopify API and cached in Redis.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/sync-products")
async def sync_ecommerce_products(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Products are read directly from Shopify API and cached in Redis.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/sync-inventory")
async def sync_ecommerce_inventory(
    connection_id: int,
    data: EcommerceProductSyncRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        return {
            "status": "skipped",
            "reason": "live_api_mode",
            "message": "Inventory is read directly from Shopify API when product data is requested.",
            "connection_id": connection.id,
        }

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/sync-checkouts")
async def sync_ecommerce_checkouts(
    connection_id: int,
    data: EcommerceSyncRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        try:
            return sync_abandoned_checkouts(sync_db, connection, data.limit)
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)

@router.get("/connections/{connection_id}/locations")
async def get_ecommerce_locations(
    connection_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        try:
            return {"status": "success", "locations": fetch_locations(connection)}
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)

@router.get("/connections/{connection_id}/contact-store-mappings")
async def list_contact_store_mappings(
    connection_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
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
            "mappings": [serialize_contact_store_mapping(row) for row in rows],
        }

    return await db.run_sync(sync_op)

@router.post("/connections/{connection_id}/contact-store-mappings")
async def save_contact_store_mapping(
    connection_id: int,
    request: Request,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: upsert_manual_contact_store_mapping(sync_db, connection_id, body, tenant_id=tenant_id)
    )

@router.get("/connections/{connection_id}/shopify-collections")
async def get_shopify_catalog_collections(
    connection_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: shopify_catalog_collections_payload(sync_db, connection_id, tenant_id=tenant_id)
    )

@router.put("/connections/{connection_id}/shopify-collections")
async def update_shopify_catalog_collections(
    connection_id: int,
    data: ShopifyCatalogCollectionUpdateRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    response = await db.run_sync(
        lambda sync_db: update_shopify_catalog_collections_payload(sync_db, connection_id, data, tenant_id=tenant_id)
    )
    await clear_shopify_catalog_cache(connection_id)
    return response

@router.get("/connections/{connection_id}/orders/{shopify_order_id}/fulfillments")
async def get_ecommerce_order_fulfillments(
    connection_id: int,
    shopify_order_id: str,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        connection = connection_or_404(sync_db, connection_id, tenant_id)
        try:
            return {
                "status": "success",
                "fulfillments": fetch_fulfillments(connection, shopify_order_id),
            }
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@router.get("/webhooks/shopify/dead-letter")
async def list_shopify_dead_letter_events(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    result = await db.execute(
        select(ShopifyWebhookEvent)
        .where(
            ShopifyWebhookEvent.tenant_id == current_user.tenant_id,
            ShopifyWebhookEvent.status == "dead_letter",
        )
        .order_by(ShopifyWebhookEvent.dead_lettered_at.desc(), ShopifyWebhookEvent.created_at.desc())
        .limit(200)
    )
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "shop_domain": row.shop_domain,
            "topic": row.topic,
            "webhook_id": row.webhook_id,
            "request_id": row.request_id,
            "attempts": row.attempts,
            "last_error": row.last_error or row.error,
            "dead_lettered_at": str(row.dead_lettered_at) if row.dead_lettered_at else None,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@router.post("/webhooks/shopify/{event_id}/reopen")
async def reopen_shopify_webhook_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    event = await db.scalar(
        select(ShopifyWebhookEvent).where(
            ShopifyWebhookEvent.id == event_id,
            ShopifyWebhookEvent.tenant_id == current_user.tenant_id,
        )
    )
    if not event:
        raise HTTPException(status_code=404, detail="Shopify webhook event not found")
    if event.status != "dead_letter":
        raise HTTPException(status_code=400, detail="Only dead-letter events can be reopened")

    event.status = "failed"
    event.dead_lettered_at = None
    event.next_retry_at = None
    await db.commit()
    return {
        "status": "reopened",
        "event_id": event.id,
        "message": "Event is visible as failed again. Replay requires the source webhook payload to be resent.",
    }

