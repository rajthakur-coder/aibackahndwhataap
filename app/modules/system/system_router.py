from fastapi import APIRouter, Depends

from app.modules.system.system_service import (
    ensure_live_tenant_config,
    health_status,
    home_status,
    readiness_status,
    runtime_config_status,
)
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


system_router = APIRouter(tags=["system"])


@system_router.get("/")
def home():
    return home_status()


@system_router.get("/health")
def health():
    return health_status()


@system_router.get("/runtime/config")
def runtime_config():
    return runtime_config_status()


@system_router.get("/readiness")
async def readiness(tenant_id: str = "default"):
    return await readiness_status(tenant_id)


@system_router.post("/runtime/tenant-config", dependencies=[Depends(get_current_user_token)])
async def seed_live_tenant_config(tenant_id: str = Depends(strict_tenant_id)):
    return await ensure_live_tenant_config(tenant_id)
