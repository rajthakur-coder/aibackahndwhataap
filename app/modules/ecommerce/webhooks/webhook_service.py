import base64
import hashlib
import hmac
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ecommerce import EcommerceConnection, ShopifyWebhookEvent
from app.modules.audit import write_audit_log

MAX_SHOPIFY_WEBHOOK_ATTEMPTS = 5

def normalize_shop_domain(value: str | None) -> str:
    return (value or "").strip().replace("https://", "").replace("http://", "").strip("/")

def verify_shopify_hmac(raw_body: bytes, hmac_header: str | None) -> bool:
    secret = settings.SHOPIFY_WEBHOOK_SECRET
    if not secret:
        return True
    if not hmac_header:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    calculated = base64.b64encode(digest).decode()
    return hmac.compare_digest(calculated, hmac_header)

def find_shopify_connection_by_domain(db: Session, shop_domain: str) -> EcommerceConnection | None:
    domain = normalize_shop_domain(shop_domain)
    return db.execute(
        select(EcommerceConnection)
        .where(
            EcommerceConnection.platform == "shopify",
            (
                (EcommerceConnection.myshopify_domain == domain)
                | (EcommerceConnection.store_url == domain)
            ),
        )
    ).scalars().first()

def record_shopify_webhook_event(
    db: Session,
    connection: EcommerceConnection,
    shop_domain: str,
    topic: str,
    webhook_id: str | None,
    raw_body: bytes,
) -> tuple[ShopifyWebhookEvent, bool]:
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    existing = None
    if webhook_id:
        existing = db.execute(
            select(ShopifyWebhookEvent).where(ShopifyWebhookEvent.webhook_id == webhook_id)
        ).scalars().first()
    if not existing:
        existing = db.execute(
            select(ShopifyWebhookEvent)
            .where(
                ShopifyWebhookEvent.connection_id == connection.id,
                ShopifyWebhookEvent.topic == topic,
                ShopifyWebhookEvent.payload_hash == payload_hash,
            )
        ).scalars().first()
    if existing:
        if existing.status == "dead_letter":
            return existing, True
        if existing.status not in {"processed", "dead_letter"}:
            existing.attempts = (existing.attempts or 0) + 1
            existing.status = "processing"
            existing.next_retry_at = None
            db.commit()
        return existing, existing.status == "processed"

    event = ShopifyWebhookEvent(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        shop_domain=normalize_shop_domain(shop_domain),
        topic=topic,
        webhook_id=webhook_id,
        request_id=None,
        payload_hash=payload_hash,
        raw_payload=raw_body.decode("utf-8", errors="replace"),
        status="processing",
        attempts=1,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, False


def set_shopify_webhook_request_id(
    db: Session,
    event: ShopifyWebhookEvent,
    request_id: str | None,
) -> None:
    if request_id and not event.request_id:
        event.request_id = request_id
        db.commit()

def mark_shopify_webhook_event(
    db: Session,
    event: ShopifyWebhookEvent,
    status: str,
    error: str | None = None,
) -> None:
    event.error = error
    event.last_error = error or event.last_error
    if status == "failed" and (event.attempts or 0) >= MAX_SHOPIFY_WEBHOOK_ATTEMPTS:
        event.status = "dead_letter"
        event.dead_lettered_at = datetime.utcnow()
        event.next_retry_at = None
        event.processed_at = None
    elif status == "failed":
        event.status = status
        delay_seconds = min(60 * (2 ** max((event.attempts or 1) - 1, 0)), 3600)
        event.next_retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
        event.processed_at = None
    else:
        event.status = status
        event.next_retry_at = None
        event.processed_at = datetime.utcnow() if status == "processed" else None
    if event.status in {"failed", "dead_letter"}:
        write_audit_log(
            db,
            action="webhook.shopify_failed",
            tenant_id=event.tenant_id,
            entity_type="shopify_webhook_event",
            entity_id=event.id,
            status=event.status,
            request_id=event.request_id,
            metadata={
                "shop_domain": event.shop_domain,
                "topic": event.topic,
                "webhook_id": event.webhook_id,
                "attempts": event.attempts,
                "dead_lettered": event.status == "dead_letter",
                "error": error,
            },
        )
    db.commit()
