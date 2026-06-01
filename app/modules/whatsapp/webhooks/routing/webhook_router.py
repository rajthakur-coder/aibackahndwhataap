from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.whatsapp import WebhookEvent
from app.security import get_current_user_token
from app.modules.whatsapp.whatsapp_schema import RetryWebhookEventsRequest
from app.modules.whatsapp.whatsapp_service import (
    UnresolvedWebhookTenantError,
    get_or_create_webhook_event,
    parse_whatsapp_messages,
    should_process_webhook_event,
    verify_meta_webhook_signature,
)
from app.shared.arq_queue import enqueue_whatsapp_webhook_event


whatsapp_webhook_router = APIRouter(tags=["whatsapp"])


@whatsapp_webhook_router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = settings.VERIFY_TOKEN

    if mode == "subscribe" and token == verify_token and challenge:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@whatsapp_webhook_router.get("/whatsapp/cloud-api/callback")
async def verify_whatsapp_cloud_callback(request: Request):
    return await verify_webhook(request)


@whatsapp_webhook_router.post("/webhook")
async def receive_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    raw_body = await request.body()
    if not verify_meta_webhook_signature(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid WhatsApp webhook signature")
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    incoming_messages = parse_whatsapp_messages(body)
    queued = 0
    skipped = 0
    unresolved = 0

    for incoming in incoming_messages:
        try:
            event, created = await db.run_sync(
                lambda sync_db: get_or_create_webhook_event(
                    sync_db,
                    incoming,
                    request_id=getattr(request.state, "request_id", None),
                )
            )
        except UnresolvedWebhookTenantError:
            unresolved += 1
            continue
        if not should_process_webhook_event(event, created):
            skipped += 1
            continue

        try:
            await enqueue_whatsapp_webhook_event(event.id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Webhook queue is unavailable") from exc
        queued += 1

    return {
        "status": "accepted",
        "received": len(incoming_messages),
        "queued": queued,
        "skipped": skipped,
        "unresolved": unresolved,
    }


@whatsapp_webhook_router.post("/whatsapp/cloud-api/callback")
async def receive_whatsapp_cloud_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await receive_webhook(request, db)


@whatsapp_webhook_router.get("/webhook/events")
async def list_webhook_events(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    statement = select(WebhookEvent).where(WebhookEvent.tenant_id == current_user.tenant_id)
    if status:
        statement = statement.where(WebhookEvent.status == status)
    result = await db.execute(statement.order_by(WebhookEvent.created_at.desc()).limit(200))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "external_id": row.external_id,
            "tenant_id": row.tenant_id,
            "request_id": row.request_id,
            "phone": row.phone,
            "message_text": row.message_text,
            "status": row.status,
            "attempts": row.attempts,
            "error": row.error,
            "last_error": row.last_error,
            "next_retry_at": str(row.next_retry_at) if row.next_retry_at else None,
            "dead_lettered_at": str(row.dead_lettered_at) if row.dead_lettered_at else None,
            "created_at": str(row.created_at),
            "processed_at": str(row.processed_at) if row.processed_at else None,
        }
        for row in rows
    ]


@whatsapp_webhook_router.get("/webhook/events/dead-letter")
async def list_dead_letter_webhook_events(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    result = await db.execute(
        select(WebhookEvent)
        .where(
            WebhookEvent.tenant_id == current_user.tenant_id,
            WebhookEvent.status == "dead_letter",
        )
        .order_by(WebhookEvent.dead_lettered_at.desc(), WebhookEvent.created_at.desc())
        .limit(200)
    )
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "external_id": row.external_id,
            "request_id": row.request_id,
            "phone": row.phone,
            "message_text": row.message_text,
            "attempts": row.attempts,
            "last_error": row.last_error or row.error,
            "dead_lettered_at": str(row.dead_lettered_at) if row.dead_lettered_at else None,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@whatsapp_webhook_router.post("/webhook/events/{event_id}/replay")
async def replay_webhook_event(
    event_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    event = await db.scalar(
        select(WebhookEvent).where(
            WebhookEvent.id == event_id,
            WebhookEvent.tenant_id == current_user.tenant_id,
        )
    )
    if not event:
        raise HTTPException(status_code=404, detail="Webhook event not found")
    if event.status not in {"failed", "dead_letter"}:
        raise HTTPException(status_code=400, detail="Only failed/dead-letter events can be replayed")

    event.status = "pending"
    event.dead_lettered_at = None
    event.next_retry_at = None
    event.error = None
    await db.commit()
    await enqueue_whatsapp_webhook_event(event.id, unique=False)
    return {"status": "queued", "event_id": event.id}


@whatsapp_webhook_router.post("/webhook/events/retry-failed")
async def retry_failed_webhook_events(
    data: RetryWebhookEventsRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user_token),
):
    result = await db.execute(
        select(WebhookEvent)
        .where(WebhookEvent.status == "failed", WebhookEvent.tenant_id == current_user.tenant_id)
        .where(WebhookEvent.dead_lettered_at.is_(None))
        .order_by(WebhookEvent.created_at.asc())
        .limit(max(1, min(data.limit, 100)))
    )
    rows = result.scalars().all()

    queued = 0
    failed = 0
    errors = []
    for event in rows:
        try:
            job_id = await enqueue_whatsapp_webhook_event(event.id, unique=False)
            if job_id:
                queued += 1
        except Exception as exc:
            failed += 1
            errors.append({"event_id": event.id, "error": str(exc)})

    return {
        "status": "queued",
        "queued": queued,
        "failed": failed,
        "errors": errors[:5],
    }
