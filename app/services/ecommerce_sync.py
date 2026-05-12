import asyncio
import json

from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.ecommerce import EcommerceConnection, EcommerceProduct
from app.models.entities import AgentAction, KnowledgeDocument
from app.services.ecommerce import product_knowledge_text, sync_orders, sync_products
from app.services.rag import save_knowledge_chunks, save_knowledge_document


def sync_product_catalog_knowledge(
    db: Session,
    connection: EcommerceConnection,
    limit: int,
) -> dict:
    products = (
        db.query(EcommerceProduct)
        .filter(EcommerceProduct.connection_id == connection.id)
        .order_by(EcommerceProduct.updated_at.desc())
        .limit(max(1, min(limit, 250)))
        .all()
    )
    source = f"ecommerce://{connection.platform}/{connection.id}/products"
    content = "\n\n---\n\n".join(product_knowledge_text(product) for product in products)
    if not content:
        return {"knowledge_source": source, "knowledge_products": 0}

    document = db.query(KnowledgeDocument).filter(KnowledgeDocument.source == source).first()
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


def sync_active_ecommerce_connections() -> dict:
    db = SessionLocal()
    try:
        connections = (
            db.query(EcommerceConnection)
            .filter(EcommerceConnection.status == "active")
            .order_by(EcommerceConnection.updated_at.asc())
            .all()
        )
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
    finally:
        db.close()


async def ecommerce_auto_sync_loop() -> None:
    await asyncio.sleep(5)
    while settings.ecommerce_auto_sync_enabled:
        await run_in_threadpool(sync_active_ecommerce_connections)
        await asyncio.sleep(settings.ecommerce_auto_sync_interval_seconds)
