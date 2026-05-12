"""FastAPI route modules."""

from app.api.routes import automations, crm, ecommerce, rag, system, whatsapp

ROUTERS = [
    system.router,
    whatsapp.router,
    rag.router,
    ecommerce.router,
    automations.router,
    crm.router,
]

__all__ = ["ROUTERS", "automations", "crm", "ecommerce", "rag", "system", "whatsapp"]
