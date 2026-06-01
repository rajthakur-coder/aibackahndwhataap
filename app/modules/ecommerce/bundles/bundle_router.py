from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingPatchRequest, BundlePairingRequest
from app.modules.ecommerce.bundles.bundle_service import (
    delete_bundle_pairing,
    list_bundle_pairings,
    patch_bundle_pairing,
    upsert_bundle_pairing,
)
from app.shared.tenant import strict_tenant_id


router = APIRouter(prefix="/bundles")


@router.get("")
async def list_bundles(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: list_bundle_pairings(sync_db, tenant_id))


@router.post("")
async def create_or_update_bundle(
    data: BundlePairingRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: upsert_bundle_pairing(sync_db, data, tenant_id))


@router.patch("/{pairing_id}")
async def patch_bundle(
    pairing_id: int,
    data: BundlePairingPatchRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.run_sync(lambda sync_db: patch_bundle_pairing(sync_db, pairing_id, data, tenant_id))
    if not result:
        raise HTTPException(status_code=404, detail="Bundle pairing not found")
    return result


@router.delete("/{pairing_id}")
async def delete_bundle(
    pairing_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    deleted = await db.run_sync(lambda sync_db: delete_bundle_pairing(sync_db, pairing_id, tenant_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Bundle pairing not found")
    return {"status": "deleted", "id": pairing_id}
