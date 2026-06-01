import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.ecommerce import ContactStoreMapping, EcommerceConnection
from app.models.ecommerce import EcommerceProduct
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

@router.get("/products")
async def list_ecommerce_products(
    connection_id: int | None = None,
    q: str | None = None,
    limit: int = 50,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db: Session):
        query = select(EcommerceProduct).where(EcommerceProduct.tenant_id == tenant_id)
        if connection_id:
            query = query.where(EcommerceProduct.connection_id == connection_id)
        if q:
            like = f"%{q.strip()}%"
            query = query.where(
                or_(
                    EcommerceProduct.title.ilike(like),
                    EcommerceProduct.sku.ilike(like),
                    EcommerceProduct.product_type.ilike(like),
                    EcommerceProduct.tags.ilike(like),
                )
            )
        rows = sync_db.execute(
            query.order_by(EcommerceProduct.updated_at.desc()).limit(max(1, min(limit, 100)))
        ).scalars().all()
        return {
            "status": "success",
            "source": "catalog_cache",
            "tenant_id": tenant_id,
            "data": [_product_payload(row) for row in rows],
        }

    return await db.run_sync(sync_op)


def _product_payload(row: EcommerceProduct) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "connection_id": row.connection_id,
        "platform": row.platform,
        "external_id": row.external_id,
        "sku": row.sku,
        "title": row.title,
        "product_type": row.product_type,
        "tags": row.tags,
        "price_min": row.price_min,
        "price_max": row.price_max,
        "currency": row.currency,
        "inventory": row.inventory,
        "product_url": row.product_url,
        "image_urls": row.image_urls,
    }

@router.post("/sync-active")
async def sync_all_active_ecommerce_connections():
    return await sync_active_ecommerce_connections()

