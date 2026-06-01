from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id

from .woocommerce_schema import (
    WoocommerceConnectRequest,
    WoocommerceDisconnectRequest,
    WoocommerceIntegrationListResponse,
    WoocommerceIntegrationResponse,
)
from .woocommerce_service import (
    connect_woocommerce,
    disconnect_woocommerce,
    get_woocommerce_status,
    list_woocommerce_connections,
)


woocommerce_integration_router = APIRouter(
    prefix="/integrations/woocommerce",
    tags=["WooCommerce Integration"],
    dependencies=[Depends(get_current_user_token)],
)


@woocommerce_integration_router.get("", response_model=WoocommerceIntegrationResponse)
async def woocommerce_status(
    provider_account_id: str | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await get_woocommerce_status(db, tenant_id, provider_account_id)


@woocommerce_integration_router.get("/connections", response_model=WoocommerceIntegrationListResponse)
async def woocommerce_connections(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await list_woocommerce_connections(db, tenant_id)


@woocommerce_integration_router.put("/connect", response_model=WoocommerceIntegrationResponse)
async def save_woocommerce_connection(
    payload: WoocommerceConnectRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await connect_woocommerce(db, tenant_id, payload)


@woocommerce_integration_router.delete("/disconnect", response_model=WoocommerceIntegrationResponse)
async def remove_woocommerce_connection(
    payload: WoocommerceDisconnectRequest | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await disconnect_woocommerce(db, tenant_id, payload)
