import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import ROUTERS
from app.core.config import settings
from app.db.schema import ensure_ecommerce_schema
from app.db.session import SessionLocal, engine
from app.models.entities import Base
from app.modules.automation.automation_service import (
    automation_processor_loop,
    ensure_default_automation_rules,
)
from app.modules.ecommerce.ecommerce_service import ecommerce_auto_sync_loop


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)
    ensure_ecommerce_schema(engine)
    db = SessionLocal()
    try:
        ensure_default_automation_rules(db)
    finally:
        db.close()

    app = FastAPI(title="AI WhatsApp Automation API", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in ROUTERS:
        app.include_router(router)

    @app.on_event("startup")
    async def start_background_loops() -> None:
        if settings.ecommerce_auto_sync_enabled:
            asyncio.create_task(ecommerce_auto_sync_loop())
        if settings.automation_processor_enabled:
            asyncio.create_task(automation_processor_loop())

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
    )
