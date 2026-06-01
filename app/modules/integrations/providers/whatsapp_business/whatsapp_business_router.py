from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id

from .whatsapp_business_schema import (
    WhatsappBusinessConnectRequest,
    WhatsappBusinessDisconnectRequest,
    WhatsappBusinessIntegrationListResponse,
    WhatsappBusinessIntegrationResponse,
)
from .whatsapp_business_service import (
    connect_whatsapp_business,
    disconnect_whatsapp_business,
    get_whatsapp_business_status,
    list_whatsapp_business_connections,
)


whatsapp_business_router = APIRouter(
    prefix="/integrations/whatsapp-business",
    tags=["WhatsApp Business Integration"],
    dependencies=[Depends(get_current_user_token)],
)


@whatsapp_business_router.get("", response_model=WhatsappBusinessIntegrationResponse)
async def whatsapp_business_status(
    provider_account_id: str | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await get_whatsapp_business_status(db, tenant_id, provider_account_id)


@whatsapp_business_router.get("/connections", response_model=WhatsappBusinessIntegrationListResponse)
async def whatsapp_business_connections(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await list_whatsapp_business_connections(db, tenant_id)


@whatsapp_business_router.put("/connect", response_model=WhatsappBusinessIntegrationResponse)
async def save_whatsapp_business_connection(
    payload: WhatsappBusinessConnectRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await connect_whatsapp_business(db, tenant_id, payload)


@whatsapp_business_router.delete("/disconnect", response_model=WhatsappBusinessIntegrationResponse)
async def remove_whatsapp_business_connection(
    payload: WhatsappBusinessDisconnectRequest | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await disconnect_whatsapp_business(db, tenant_id, payload)
