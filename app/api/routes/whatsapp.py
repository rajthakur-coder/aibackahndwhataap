import os

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.session import SessionLocal, get_db
from app.models.entities import Message, WebhookEvent
from app.schemas import RetryWebhookEventsRequest, SendMessageRequest
from app.services.messages import save_message
from app.services.webhook_processor import (
    get_or_create_webhook_event,
    mark_webhook_event_failed,
    parse_whatsapp_messages,
    process_webhook_event,
    should_process_webhook_event,
)
from app.services.whatsapp import send_whatsapp_message


router = APIRouter(tags=["whatsapp"])


async def process_webhook_event_background(event_id: int) -> None:
    db = SessionLocal()
    event = None
    try:
        event = db.query(WebhookEvent).filter(WebhookEvent.id == event_id).first()
        if event:
            await process_webhook_event(event, db)
    except Exception as exc:
        if event:
            mark_webhook_event_failed(db, event, exc)
    finally:
        db.close()


@router.post("/send-message")
async def send_message(
    data: SendMessageRequest,
    db: Session = Depends(get_db),
):
    try:
        response = await run_in_threadpool(send_whatsapp_message, data.phone, data.message)
        save_message(db, data.phone, data.message, "outgoing")
        return {"status": "sent", "whatsapp": response}
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = os.getenv("VERIFY_TOKEN")

    if mode == "subscribe" and token == verify_token and challenge:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    incoming_messages = parse_whatsapp_messages(body)
    queued = 0
    skipped = 0

    for incoming in incoming_messages:
        event, created = get_or_create_webhook_event(db, incoming)
        if not should_process_webhook_event(event, created):
            skipped += 1
            continue

        background_tasks.add_task(process_webhook_event_background, event.id)
        queued += 1

    return {
        "status": "accepted",
        "received": len(incoming_messages),
        "queued": queued,
        "skipped": skipped,
    }


@router.get("/webhook/events")
def list_webhook_events(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(WebhookEvent)
    if status:
        query = query.filter(WebhookEvent.status == status)

    rows = query.order_by(WebhookEvent.created_at.desc()).limit(200).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "external_id": row.external_id,
            "phone": row.phone,
            "message_text": row.message_text,
            "status": row.status,
            "attempts": row.attempts,
            "error": row.error,
            "created_at": str(row.created_at),
            "processed_at": str(row.processed_at) if row.processed_at else None,
        }
        for row in rows
    ]


@router.post("/webhook/events/retry-failed")
async def retry_failed_webhook_events(
    data: RetryWebhookEventsRequest,
    db: Session = Depends(get_db),
):
    rows = (
        db.query(WebhookEvent)
        .filter(WebhookEvent.status == "failed")
        .order_by(WebhookEvent.created_at.asc())
        .limit(max(1, min(data.limit, 100)))
        .all()
    )

    retried = 0
    failed = 0
    errors = []
    for event in rows:
        try:
            await process_webhook_event(event, db)
            retried += 1
        except Exception as exc:
            mark_webhook_event_failed(db, event, exc)
            failed += 1
            errors.append({"event_id": event.id, "error": str(exc)})

    return {
        "status": "completed",
        "retried": retried,
        "failed": failed,
        "errors": errors[:5],
    }


@router.get("/conversations")
def get_conversations(db: Session = Depends(get_db)):
    conversations = db.query(Message.phone).distinct().all()
    return [{"phone": conversation[0]} for conversation in conversations]


@router.get("/messages/{phone}")
def get_messages(phone: str, db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.phone == phone)
        .order_by(Message.created_at.asc())
        .all()
    )

    return [
        {
            "id": message.id,
            "phone": message.phone,
            "message": message.message,
            "direction": message.direction,
            "created_at": str(message.created_at),
        }
        for message in messages
    ]
