import asyncio
import json

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.models.ecommerce import EcommerceConnection, ShopifyWebhookEvent
from app.models.crm import AgentAction
from app.modules.ecommerce.core.ecommerce_core_service import sync_abandoned_checkouts


def sync_active_ecommerce_connections_with_session(db: Session) -> dict:
    if not settings.ecommerce_auto_sync_checkouts_enabled:
        return {
            "status": "skipped",
            "reason": "checkout_auto_sync_disabled",
            "message": "Shopify abandoned checkout auto-sync is paused. Use /ecommerce/abandoned-cart for manual tests.",
            "connections": 0,
            "synced": 0,
            "failed": 0,
            "results": [],
        }

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
            if connection.platform == "shopify":
                result = sync_abandoned_checkouts(db, connection, settings.ecommerce_auto_sync_limit)
            else:
                result = {"status": "skipped", "reason": "live_api_mode"}
            synced += result.get("queued", 0)
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
