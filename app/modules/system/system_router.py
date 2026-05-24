from fastapi import APIRouter

from app.modules.system.system_service import health_status, home_status, runtime_config_status


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
