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

@router.post("/abandoned-cart")
async def add_abandoned_cart(
    data: AbandonedCartRequest,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: queue_manual_abandoned_cart(sync_db, data))

@router.get("/orders")
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

@router.get("/orders/{order_id}")
async def get_ecommerce_order(order_id: str, db: AsyncSession = Depends(get_db)):
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Order details are read directly from the ecommerce API when requested; Neon is not used as the order store.",
        "order_id": order_id,
        "data": None,
    }

@router.get("/customers")
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

@router.post("/automations/delivered-followups")
async def run_delivered_followups(
    data: DeliveredFollowupRequest,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: send_delivered_followups(sync_db, data.limit))

