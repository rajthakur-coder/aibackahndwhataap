import asyncio
import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.ecommerce import EcommerceConnection, EcommerceProduct, ShopifyWebhookEvent
from app.models.crm import AgentAction
from app.models.rag import KnowledgeDocument
from app.modules.ecommerce.core.ecommerce_core_service import product_knowledge_text, sync_customers, sync_orders, sync_products
from app.modules.rag.core.rag_core_service import save_knowledge_chunks, save_knowledge_document


def sync_product_catalog_knowledge(
    db: Session,
    connection: EcommerceConnection,
    limit: int,
) -> dict:
    products = db.execute(
        select(EcommerceProduct)
        .where(EcommerceProduct.connection_id == connection.id)
        .order_by(EcommerceProduct.updated_at.desc())
        .limit(max(1, min(limit, 250)))
    ).scalars().all()
    source = f"ecommerce://{connection.platform}/{connection.id}/products"
    content = "\n\n---\n\n".join(product_knowledge_text(product) for product in products)
    if not content:
        return {"knowledge_source": source, "knowledge_products": 0}

    document = db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.source == source)
    ).scalars().first()
    if document:
        document.title = f"{connection.name} product catalog"
        document.content = content
        db.commit()
        db.refresh(document)
        save_knowledge_chunks(db, document)
    else:
        save_knowledge_document(
            db,
            title=f"{connection.name} product catalog",
            source=source,
            content=content,
        )

    return {"knowledge_source": source, "knowledge_products": len(products)}


def sync_active_ecommerce_connections_with_session(db: Session) -> dict:
    connections = db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.asc())
    ).scalars().all()
    results = []
    synced = 0
    failed = 0
    for connection in connections:
        try:
            result = sync_orders(db, connection, settings.ecommerce_auto_sync_limit)
            if settings.ecommerce_auto_sync_products_enabled:
                product_result = sync_products(
                    db,
                    connection,
                    settings.ecommerce_auto_sync_product_limit,
                )
                product_result.update(
                    sync_product_catalog_knowledge(
                        db,
                        connection,
                        settings.ecommerce_auto_sync_product_limit,
                    )
                )
                result["products"] = product_result
            if connection.platform == "shopify":
                result["customers"] = sync_customers(db, connection, settings.ecommerce_auto_sync_limit)
            synced += result.get("synced", 0)
            results.append({"connection_id": connection.id, **result})
        except Exception as exc:
            failed += 1
            db.add(
                AgentAction(
                    action_type="ecommerce_auto_sync_failed",
                    status="failed",
                    payload=json.dumps({"connection_id": connection.id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            results.append(
                {
                    "connection_id": connection.id,
                    "status": "failed",
                    "error": str(exc),
                }
            )

    return {
        "status": "completed",
        "connections": len(connections),
        "synced": synced,
        "failed": failed,
        "results": results,
    }


async def sync_active_ecommerce_connections() -> dict:
    async with AsyncSessionLocal() as db:
        return await db.run_sync(sync_active_ecommerce_connections_with_session)


def retry_failed_shopify_webhooks_with_session(db: Session, limit: int = 25) -> dict:
    rows = db.execute(
        select(ShopifyWebhookEvent)
        .where(ShopifyWebhookEvent.status == "failed", ShopifyWebhookEvent.attempts < 5)
        .order_by(ShopifyWebhookEvent.updated_at.asc())
        .limit(max(1, min(limit, 100)))
    ).scalars().all()
    for row in rows:
        row.status = "pending"
        row.attempts = (row.attempts or 0) + 1
        row.error = None
    db.commit()
    return {"status": "queued", "webhooks": len(rows)}


async def retry_failed_shopify_webhooks(limit: int = 25) -> dict:
    async with AsyncSessionLocal() as db:
        return await db.run_sync(
            lambda sync_db: retry_failed_shopify_webhooks_with_session(sync_db, limit)
        )


async def ecommerce_auto_sync_loop() -> None:
    await asyncio.sleep(5)
    while settings.ecommerce_auto_sync_enabled:
        await sync_active_ecommerce_connections()
        await retry_failed_shopify_webhooks()
        await asyncio.sleep(settings.ecommerce_auto_sync_interval_seconds)
