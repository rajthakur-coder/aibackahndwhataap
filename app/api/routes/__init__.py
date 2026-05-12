"""FastAPI route modules."""

from app.api.routes import crm, rag, system, whatsapp
from app.modules.automation import automation_router
from app.modules.ecommerce import ecommerce_router

ROUTERS = [
    system.router,
    whatsapp.router,
    rag.router,
    ecommerce_router.router,
    automation_router.router,
    crm.router,
]

__all__ = [
    "ROUTERS",
    "automation_router",
    "crm",
    "ecommerce_router",
    "rag",
    "system",
    "whatsapp",
]
