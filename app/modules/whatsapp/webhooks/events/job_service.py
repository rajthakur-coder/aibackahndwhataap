import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.whatsapp import WebhookEvent, WhatsappInteractionEvent
from app.modules.whatsapp.webhooks.events.event_service import mark_webhook_event_failed
from app.modules.whatsapp.webhooks.runtime.processor_service import process_webhook_event
from app.modules.whatsapp.webhooks.responses.response_service import (
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
            sync_db.rollback()
            mark_webhook_event_failed(sync_db, event, exc)
            raise


async def process_whatsapp_webhook_event(ctx, event_id: int) -> None:
    await asyncio.to_thread(process_webhook_event_with_session, event_id)


async def process_whatsapp_cross_sell(ctx, phone: str, text: str, base_products: list[dict], queued_at: str | None = None) -> None:
    with SessionLocal() as sync_db:
        if _has_buy_now_click_since(sync_db, phone, queued_at):
            return
        await send_cross_sell_products(sync_db, phone, text, base_products)


def _has_buy_now_click_since(sync_db, phone: str, queued_at: str | None) -> bool:
    if not phone:
        return False
    since = _parse_queued_at(queued_at)
    statement = select(WhatsappInteractionEvent.id).where(
        WhatsappInteractionEvent.phone == phone,
        WhatsappInteractionEvent.event_type == "link_click",
        WhatsappInteractionEvent.source.in_(["carousel", "cta_url", "product_list", "catalog_text"]),
    )
    if since is not None:
        statement = statement.where(WhatsappInteractionEvent.created_at >= since)
    return bool(sync_db.execute(statement.order_by(WhatsappInteractionEvent.created_at.desc()).limit(1)).first())


def _parse_queued_at(value: str | None):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc)
    return parsed.replace(tzinfo=timezone.utc)


async def process_whatsapp_product_images(
    ctx,
    phone: str,
    products: list[dict],
    caption_mode: str,
    failure_action: str,
) -> None:
    with SessionLocal() as sync_db:
        await send_product_images(sync_db, phone, products, caption_mode, failure_action)
