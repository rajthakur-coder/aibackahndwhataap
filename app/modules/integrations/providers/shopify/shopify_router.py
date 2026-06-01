from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id

from .shopify_schema import (
    ShopifyConnectRequest,
    ShopifyDisconnectRequest,
    ShopifyIntegrationListResponse,
    ShopifyIntegrationResponse,
)
from .shopify_service import (
    connect_shopify,
    disconnect_shopify,
    get_shopify_status,
    list_shopify_connections,
)


shopify_integration_router = APIRouter(
    prefix="/integrations/shopify",
    tags=["Shopify Integration"],
    dependencies=[Depends(get_current_user_token)],
)


@shopify_integration_router.get("", response_model=ShopifyIntegrationResponse)
async def shopify_status(
    provider_account_id: str | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await get_shopify_status(db, tenant_id, provider_account_id)


@shopify_integration_router.get("/connections", response_model=ShopifyIntegrationListResponse)
async def shopify_connections(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await list_shopify_connections(db, tenant_id)


@shopify_integration_router.put("/connect", response_model=ShopifyIntegrationResponse)
async def save_shopify_connection(
    payload: ShopifyConnectRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await connect_shopify(db, tenant_id, payload)


@shopify_integration_router.delete("/disconnect", response_model=ShopifyIntegrationResponse)
async def remove_shopify_connection(
    payload: ShopifyDisconnectRequest | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await disconnect_shopify(db, tenant_id, payload)
