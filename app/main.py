import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db.session import AsyncSessionLocal, Base, engine
from app.modules.automation.automation_router import automation_router
from app.modules.automation.automation_service import (
    automation_processor_loop,
    ensure_default_automation_rules,
)
from app.modules.crm.crm_router import crm_router
from app.modules.ecommerce.ecommerce_router import (
    ecommerce_router,
    shopify_webhooks_router,
)
from app.modules.knowledge.knowledge_router import knowledge_router
from app.modules.scraper.scraper_router import scraper_router
from app.modules.system.system_router import system_router
from app.modules.whatsapp.whatsapp_router import whatsapp_router
from app.shared.arq_queue import close_arq_pools
from app.shared.redis import close_redis


ROUTERS = [
    system_router,
    whatsapp_router,
    ecommerce_router,
    shopify_webhooks_router,
    scraper_router,
    knowledge_router,
    automation_router,
    crm_router,
]


def ensure_live_chat_message_columns(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("messages"):
        return

    existing = {column["name"] for column in inspector.get_columns("messages")}
    columns = {
        "status": "VARCHAR",
        "message_type": "VARCHAR",
        "whatsapp_message_id": "VARCHAR",
    }
    for name, ddl_type in columns.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE messages ADD COLUMN {name} {ddl_type}"))


def ensure_contact_columns(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("contacts"):
        return

    existing = {column["name"] for column in inspector.get_columns("contacts")}
    columns = {
        "profile_name": "VARCHAR",
        "custom_name": "VARCHAR",
        "remark": "TEXT",
        "status": "VARCHAR DEFAULT 'Active'",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
    }
    for name, ddl_type in columns.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE contacts ADD COLUMN {name} {ddl_type}"))


def ensure_bot_settings_columns(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("bot_settings"):
        return

    existing = {column["name"] for column in inspector.get_columns("bot_settings")}
    columns = {
        "ai_personality": "VARCHAR",
        "ai_tone": "VARCHAR",
        "response_length": "VARCHAR",
        "custom_instructions": "TEXT",
    }
    for name, ddl_type in columns.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE bot_settings ADD COLUMN {name} {ddl_type}"))


def ensure_contact_store_mapping_columns(connection) -> None:
    inspector = inspect(connection)
    if not inspector.has_table("contact_store_mappings"):
        return

    existing = {column["name"] for column in inspector.get_columns("contact_store_mappings")}
    columns = {
        "last_seen_at": "TIMESTAMP",
    }
    for name, ddl_type in columns.items():
        if name not in existing:
            connection.execute(text(f"ALTER TABLE contact_store_mappings ADD COLUMN {name} {ddl_type}"))


def initialize_database_schema(connection) -> None:
    is_postgres = connection.dialect.name == "postgresql"
    if is_postgres:
        connection.execute(text("SELECT pg_advisory_lock(hashtext('ai_whatsapp_schema_init'))"))

    try:
        Base.metadata.create_all(bind=connection)
        ensure_live_chat_message_columns(connection)
        ensure_contact_columns(connection)
        ensure_bot_settings_columns(connection)
        ensure_contact_store_mapping_columns(connection)
    finally:
        if is_postgres:
            connection.execute(text("SELECT pg_advisory_unlock(hashtext('ai_whatsapp_schema_init'))"))


def create_app() -> FastAPI:
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
        if settings.init_db_on_startup:
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(initialize_database_schema)
            except SQLAlchemyError as exc:
                print(f"Database schema initialization skipped: {exc}")
            async with AsyncSessionLocal() as db:
                await db.run_sync(ensure_default_automation_rules)
        if settings.automation_processor_enabled:
            asyncio.create_task(automation_processor_loop())

    @app.on_event("shutdown")
    async def close_background_resources() -> None:
        await close_arq_pools()
        await close_redis()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
    )
