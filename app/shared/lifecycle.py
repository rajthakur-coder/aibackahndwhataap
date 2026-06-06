import asyncio
from contextlib import suppress

from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.modules.automation.automation_service import automation_processor_loop
from app.modules.ecommerce.sync.sync_service import ecommerce_auto_sync_loop
from app.shared.arq_queue import close_arq_pools
from app.shared.redis import close_redis
from app.shared.schema_init import initialize_database_schema


async def initialize_database() -> None:
    if not settings.INIT_DB_ON_STARTUP:
        return

    try:
        async with engine.begin() as connection:
            await connection.run_sync(initialize_database_schema)
    except SQLAlchemyError as exc:
        print(f"Database schema initialization skipped: {exc}")


def register_lifecycle_events(app: FastAPI) -> None:
    @app.on_event("startup")
    async def startup() -> None:
        await initialize_database()

        if settings.AUTOMATION_PROCESSOR_ENABLED:
            app.state.automation_processor_task = asyncio.create_task(
                automation_processor_loop()
            )
        if settings.ECOMMERCE_AUTO_SYNC_CHECKOUTS_ENABLED:
            app.state.ecommerce_auto_sync_task = asyncio.create_task(
                ecommerce_auto_sync_loop()
            )

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = getattr(app.state, "automation_processor_task", None)
        ecommerce_task = getattr(app.state, "ecommerce_auto_sync_task", None)

        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if ecommerce_task:
            ecommerce_task.cancel()
            with suppress(asyncio.CancelledError):
                await ecommerce_task

        await close_arq_pools()
        await close_redis()
