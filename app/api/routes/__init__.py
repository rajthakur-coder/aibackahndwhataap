"""FastAPI route modules."""

from app.api.routes import crm, ecommerce, rag, system, whatsapp

ROUTERS = [
    system.router,
    whatsapp.router,
    rag.router,
    ecommerce.router,
    crm.router,
]

__all__ = ["ROUTERS", "crm", "ecommerce", "rag", "system", "whatsapp"]
