from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.audit import write_async_audit_log
from app.modules.tenants.agency_service import agency_overview, list_agency_clients, upsert_agency_client, white_label_profile
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


agency_router = APIRouter(prefix="/agency", tags=["agency"], dependencies=[Depends(get_current_user_token)])


@agency_router.get("/overview")
async def overview(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: agency_overview(sync_db, tenant_id))


@agency_router.get("/clients")
async def clients(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: list_agency_clients(sync_db, tenant_id))


@agency_router.post("/clients")
async def save_client(request: Request, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    try:
        result = await db.run_sync(lambda sync_db: upsert_agency_client(sync_db, tenant_id, payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_async_audit_log(
        db,
        action="agency.client_upserted",
        tenant_id=tenant_id,
        entity_type="agency_tenant_access",
        entity_id=result["id"],
        metadata={"client_tenant_id": result["client_tenant_id"], "status": result["status"]},
        commit=True,
    )
    return result


@agency_router.get("/white-label")
async def white_label(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: white_label_profile(sync_db, tenant_id))
