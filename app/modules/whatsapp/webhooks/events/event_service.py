import json
import hashlib
import hmac
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.whatsapp import WebhookEvent, WhatsappCredential
from app.config import settings
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id
from app.modules.audit import write_audit_log
from app.modules.whatsapp.webhooks.responses.response_service import CATALOG_CATEGORY_LABELS

MAX_WEBHOOK_ATTEMPTS = 5


class UnresolvedWebhookTenantError(ValueError):
    pass


def verify_meta_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    secret = settings.WHATSAPP_WEBHOOK_APP_SECRET or settings.META_APP_SECRET
    if not secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def parse_whatsapp_messages(payload: dict) -> list[dict]:
    parsed_messages = []

    for entry in payload.get("entry", []):
        waba_id = entry.get("id")
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            display_phone_number = metadata.get("display_phone_number")
            for message in value.get("messages", []):
                message_id = message.get("id")
                text = message.get("text", {}).get("body")
                if not text and message.get("type") == "image":
                    text = message.get("image", {}).get("caption") or "[image]"
                phone = message.get("from")
                interactive = message.get("interactive") or {}
                list_reply = interactive.get("list_reply") if interactive.get("type") == "list_reply" else None
                button_reply = interactive.get("button_reply") if interactive.get("type") == "button_reply" else None
                if list_reply:
                    reply_id = str(list_reply.get("id") or "")
                    title = str(list_reply.get("title") or "")
                    if reply_id.startswith("catalog:page:"):
                        text = f"catalog page {reply_id.removeprefix('catalog:page:')}".strip()
                    elif reply_id.startswith("catalog:category:"):
                        text = f"catalog dynamic category {reply_id.removeprefix('catalog:category:')} {title}".strip()
                    elif reply_id.startswith("catalog:"):
                        text = f"catalog category {reply_id.removeprefix('catalog:')} {title}".strip()
                    else:
                        text = title or reply_id
                elif button_reply:
                    reply_id = str(button_reply.get("id") or "")
                    title = str(button_reply.get("title") or "")
                    if reply_id.startswith("catalog:more:"):
                        parts = reply_id.split(":")
                        category = parts[2] if len(parts) > 2 else ""
                        page = parts[3] if len(parts) > 3 else "1"
                        if category in CATALOG_CATEGORY_LABELS:
                            text = f"catalog category {category} page {page}".strip()
                        else:
                            text = f"catalog dynamic category {category} page {page}".strip()
                    elif reply_id == "menu:catalog":
                        text = "show catalog"
                    elif reply_id == "menu:order_status":
                        text = "track order"
                    elif reply_id == "menu:human":
                        text = "talk to human"
                    else:
                        text = title or reply_id

                if phone and text:
                    parsed_messages.append(
                        {
                            "id": message_id,
                            "phone": phone,
                            "text": text,
                            "payload": message,
                            "phone_number_id": phone_number_id,
                            "display_phone_number": display_phone_number,
                            "waba_id": waba_id,
                        }
                    )

    return parsed_messages


def get_or_create_webhook_event(
    db: Session,
    incoming: dict,
    request_id: str | None = None,
) -> tuple[WebhookEvent, bool]:
    external_id = incoming.get("id")
    event = None
    if external_id:
        event = db.execute(
            select(WebhookEvent).where(WebhookEvent.external_id == external_id)
        ).scalars().first()
    if event:
        return event, False

    tenant_id = resolve_whatsapp_webhook_tenant_id(db, incoming)
    if not tenant_id:
        raise UnresolvedWebhookTenantError(
            "Could not resolve tenant for WhatsApp webhook. Configure a WhatsApp credential for the received phone_number_id, display_phone_number, or WABA ID."
        )

    event = WebhookEvent(
        tenant_id=tenant_id,
        external_id=external_id,
        request_id=request_id,
        phone=incoming["phone"],
        message_text=incoming["text"],
        payload=json.dumps(
            {
                "message": incoming.get("payload") or {},
                "phone_number_id": incoming.get("phone_number_id"),
                "display_phone_number": incoming.get("display_phone_number"),
                "waba_id": incoming.get("waba_id"),
            },
            ensure_ascii=True,
        ),
        status="pending",
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event, True


def resolve_whatsapp_webhook_tenant_id(db: Session, incoming: dict) -> str | None:
    explicit = normalize_tenant_id(incoming.get("tenant_id")) if incoming.get("tenant_id") else None
    if explicit and explicit != DEFAULT_TENANT_ID:
        return explicit

    phone_number_id = str(incoming.get("phone_number_id") or "").strip()
    raw_display_phone_number = str(incoming.get("display_phone_number") or "").strip()
    display_phone_number = _digits(raw_display_phone_number)
    waba_id = str(incoming.get("waba_id") or "").strip()
    if not any((phone_number_id, display_phone_number, waba_id)):
        return None

    try:
        candidates = []
        if phone_number_id:
            candidates.append(WhatsappCredential.phone_number_id == phone_number_id)
        if raw_display_phone_number:
            candidates.append(WhatsappCredential.phone_number == raw_display_phone_number)
        if display_phone_number:
            candidates.append(WhatsappCredential.phone_number == display_phone_number)
        if waba_id:
            candidates.append(WhatsappCredential.waba_id == waba_id)
        credential = db.execute(
            select(WhatsappCredential)
            .where(
                WhatsappCredential.tenant_id != DEFAULT_TENANT_ID,
                WhatsappCredential.status == "active",
                or_(*candidates),
            )
            .order_by(WhatsappCredential.updated_at.desc())
            .limit(1)
        ).scalars().first()
    except SQLAlchemyError:
        db.rollback()
        return None
    tenant_id = normalize_tenant_id(getattr(credential, "tenant_id", None))
    return tenant_id if tenant_id != DEFAULT_TENANT_ID else None


def _digits(value: str | None) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def should_process_webhook_event(event: WebhookEvent, created: bool) -> bool:
    if created:
        return True
    if event.status == "dead_letter":
        return False
    if event.next_retry_at and event.next_retry_at > datetime.utcnow():
        return False
    return event.status in {"pending", "failed"}


def mark_webhook_event_failed(db: Session, event: WebhookEvent, exc: Exception) -> None:
    attempts = event.attempts or 0
    event.error = str(exc)
    event.last_error = str(exc)
    if attempts >= MAX_WEBHOOK_ATTEMPTS:
        event.status = "dead_letter"
        event.dead_lettered_at = datetime.utcnow()
        event.next_retry_at = None
    else:
        event.status = "failed"
        delay_seconds = min(60 * (2 ** max(attempts - 1, 0)), 3600)
        event.next_retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
    write_audit_log(
        db,
        action="webhook.whatsapp_failed",
        tenant_id=event.tenant_id,
        entity_type="webhook_event",
        entity_id=event.id,
        status=event.status,
        request_id=event.request_id,
        metadata={
            "external_id": event.external_id,
            "attempts": attempts,
            "dead_lettered": event.status == "dead_letter",
            "error": str(exc),
        },
    )
    db.commit()
