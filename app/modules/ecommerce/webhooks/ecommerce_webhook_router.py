import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ecommerce import ContactStoreMapping, EcommerceConnection
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

router = APIRouter()

@router.post("/connections/{connection_id}/webhook/order")
async def receive_ecommerce_order_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid ecommerce webhook JSON") from exc

    return await db.run_sync(
        lambda sync_db: handle_ecommerce_order_webhook(sync_db, connection_id, body)
    )

@router.post("/connections/{connection_id}/webhook/abandoned-cart")
async def receive_abandoned_cart_webhook(
    connection_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid abandoned cart webhook JSON") from exc

    return await db.run_sync(
        lambda sync_db: handle_abandoned_cart_webhook(sync_db, connection_id, body)
    )

