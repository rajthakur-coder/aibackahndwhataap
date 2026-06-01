from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import IntegrationProvider, IntegrationStatus
from app.modules.integrations.integrations_service import (
    disconnect_integration,
    get_integration,
    list_integrations,
    serialize_integration,
    upsert_integration,
)
from .shopify_schema import SHOPIFY_STATUSES, ShopifyConnectRequest, ShopifyDisconnectRequest


PROVIDER = IntegrationProvider.SHOPIFY


def _normalize_status(status: str | None) -> str:
    value = (status or IntegrationStatus.CONNECTED).strip().upper()
    if value not in SHOPIFY_STATUSES:
        raise HTTPException(status_code=400, detail=f"Unsupported integration status: {status}")
    return value


async def get_shopify_status(
    db: AsyncSession,
    tenant_id: str,
    provider_account_id: str | None = None,
) -> dict:
    row = await db.run_sync(
        lambda sync_db: get_integration(
            sync_db,
            tenant_id=tenant_id,
            provider=PROVIDER,
            provider_account_id=provider_account_id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shopify integration not found")
    return {"status": "success", "integration": serialize_integration(row)}


async def list_shopify_connections(db: AsyncSession, tenant_id: str) -> dict:
    rows = await db.run_sync(lambda sync_db: list_integrations(sync_db, tenant_id))
    return {
        "status": "success",
        "integrations": [
            serialize_integration(row)
            for row in rows
            if row.provider == PROVIDER
        ],
    }


async def connect_shopify(
    db: AsyncSession,
    tenant_id: str,
    payload: ShopifyConnectRequest,
) -> dict:
    if payload.provider and payload.provider.strip().upper() != PROVIDER:
        raise HTTPException(status_code=400, detail="Provider path and body do not match")

    row = await db.run_sync(
        lambda sync_db: upsert_integration(
            sync_db,
            tenant_id=tenant_id,
            provider=PROVIDER,
            scopes=payload.scopes,
            access_token=payload.access_token,
            refresh_token=payload.refresh_token,
            provider_account_id=payload.provider_account_id,
            display_name=payload.display_name,
            status=_normalize_status(payload.status),
        )
    )
    return {"status": "success", "integration": serialize_integration(row)}


async def disconnect_shopify(
    db: AsyncSession,
    tenant_id: str,
    payload: ShopifyDisconnectRequest | None = None,
) -> dict:
    row = await db.run_sync(
        lambda sync_db: disconnect_integration(
            sync_db,
            tenant_id=tenant_id,
            provider=PROVIDER,
            provider_account_id=payload.provider_account_id if payload else None,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Shopify integration not found")
    return {"status": "success", "integration": serialize_integration(row)}
