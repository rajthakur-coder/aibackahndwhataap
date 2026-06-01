from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.audit import write_async_audit_log
from app.modules.tenants.tenant_schema import TenantConfigRequest, TenantTemplateSeedRequest
from app.modules.tenants.tenant_service import (
    get_tenant_config,
    seed_tenant_config,
    serialize_tenant_config,
    upsert_tenant_config,
)
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


tenants_router = APIRouter(
    prefix="/tenants",
    tags=["tenants"],
    dependencies=[Depends(get_current_user_token)],
)


@tenants_router.get("/current/config")
async def get_current_tenant_config(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    row = await db.run_sync(lambda sync_db: get_tenant_config(sync_db, tenant_id))
    if not row:
        raise HTTPException(status_code=404, detail="Tenant config not found")
    return {"status": "success", "data": serialize_tenant_config(row)}


@tenants_router.put("/current/config")
async def update_current_tenant_config(
    data: TenantConfigRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    row = await db.run_sync(lambda sync_db: upsert_tenant_config(sync_db, data, tenant_id))
    payload = serialize_tenant_config(row)
    await write_async_audit_log(
        db,
        action="tenant_config.updated",
        tenant_id=tenant_id,
        entity_type="tenant_config",
        entity_id=tenant_id,
        metadata={"brand_name": payload.get("brand_name")},
        commit=True,
    )
    return {"status": "success", "data": payload}


@tenants_router.post("/current/config/seed")
async def seed_current_tenant_config(
    data: TenantTemplateSeedRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await db.run_sync(
            lambda sync_db: seed_tenant_config(sync_db, tenant_id, template=data.template, overwrite=data.overwrite)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = serialize_tenant_config(row)
    await write_async_audit_log(
        db,
        action="tenant_config.seeded",
        tenant_id=tenant_id,
        entity_type="tenant_config",
        entity_id=tenant_id,
        metadata={"template": data.template, "overwrite": data.overwrite},
        commit=True,
    )
    return {"status": "success", "data": payload}
