import asyncio

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.whatsapp import WebhookEvent
from app.modules.whatsapp.core.webhook_processor_service import (
    mark_webhook_event_failed,
    process_webhook_event,
)
from app.modules.whatsapp.core.webhook_response_service import (
    send_cross_sell_products,
    send_product_images,
)


def process_webhook_event_with_session(event_id: int) -> None:
    with SessionLocal() as sync_db:
        event = sync_db.execute(
            select(WebhookEvent).where(WebhookEvent.id == event_id)
        ).scalars().first()
        if not event:
            return
        try:
            asyncio.run(process_webhook_event(event, sync_db))
        except Exception as exc:
            mark_webhook_event_failed(sync_db, event, exc)
            raise


async def process_whatsapp_webhook_event(ctx, event_id: int) -> None:
    await asyncio.to_thread(process_webhook_event_with_session, event_id)


async def process_whatsapp_cross_sell(ctx, phone: str, text: str, base_products: list[dict]) -> None:
    with SessionLocal() as sync_db:
        await send_cross_sell_products(sync_db, phone, text, base_products)


async def process_whatsapp_product_images(
    ctx,
    phone: str,
    products: list[dict],
    caption_mode: str,
    failure_action: str,
) -> None:
    with SessionLocal() as sync_db:
        await send_product_images(sync_db, phone, products, caption_mode, failure_action)
