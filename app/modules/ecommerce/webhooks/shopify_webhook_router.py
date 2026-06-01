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


ecommerce_router = APIRouter(prefix="/ecommerce", tags=["ecommerce"])

router = APIRouter()

@router.post("/orders")
async def shopify_orders_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers
    return await db.run_sync(
        lambda sync_db: handle_shopify_orders_webhook(
            sync_db,
            raw_body,
            headers,
            request_id=getattr(request.state, "request_id", None),
        )
    )

@router.post("/products")
async def shopify_products_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers
    return await db.run_sync(
        lambda sync_db: handle_shopify_products_webhook(
            sync_db,
            raw_body,
            headers,
            request_id=getattr(request.state, "request_id", None),
        )
    )

@router.post("/fulfillments")
async def shopify_fulfillments_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    headers = request.headers
    return await db.run_sync(
        lambda sync_db: handle_shopify_fulfillments_webhook(
            sync_db,
            raw_body,
            headers,
            request_id=getattr(request.state, "request_id", None),
        )
    )

