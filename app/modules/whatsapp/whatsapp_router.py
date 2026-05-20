import asyncio

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.db.session import AsyncSessionLocal, SessionLocal, get_db
from app.models.whatsapp import Message, WebhookEvent
from app.modules.whatsapp.core.live_chat_service import (
    assign_tags_to_contact,
    create_tag,
    delete_contact,
    get_chat_messages,
    list_chat_contacts,
    list_tags,
    mark_chat_read,
    remove_tag_from_contact,
    send_live_chat_text,
    update_contact_status,
    upsert_manual_contact,
)
from app.modules.whatsapp.core.live_chat_socket import live_chat_manager
from app.modules.whatsapp.core import template_service
from app.modules.whatsapp.whatsapp_schema import (
    RetryWebhookEventsRequest,
    SendMessageRequest,
    WhatsappNumberSetupRequest,
)
from app.modules.whatsapp.whatsapp_service import (
    get_whatsapp_credential,
    get_or_create_webhook_event,
    mark_webhook_event_failed,
    parse_whatsapp_messages,
    process_webhook_event,
    save_message,
    serialize_whatsapp_credential,
    send_whatsapp_message,
    should_process_webhook_event,
    setup_whatsapp_business,
)


whatsapp_router = APIRouter(tags=["whatsapp"])


@whatsapp_router.websocket("/ws/live-chat")
async def live_chat_websocket(websocket: WebSocket):
    await live_chat_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        live_chat_manager.disconnect(websocket)
    except Exception:
        live_chat_manager.disconnect(websocket)


@whatsapp_router.post("/whatsapp-credential/number-setup")
async def setup_whatsapp_number(
    data: WhatsappNumberSetupRequest,
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db):
        try:
            credential = setup_whatsapp_business(
                sync_db,
                authorization_token=data.authorization_token,
                phone_number_id=data.phone_number_id,
                waba_id=data.waba_id,
                business_id=data.business_id,
            )
            return {
                "success": True,
                "statusCode": 1,
                "message": "WhatsApp Business setup completed successfully",
                "data": serialize_whatsapp_credential(credential),
            }
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await db.run_sync(sync_op)


@whatsapp_router.get("/whatsapp-credential/get")
async def get_my_whatsapp_credential(db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db):
        credential = get_whatsapp_credential(sync_db)
        if not credential:
            return {
                "success": False,
                "statusCode": 0,
                "message": "WhatsApp credential not found",
                "data": None,
            }
        return {
            "success": True,
            "statusCode": 1,
            "message": "WhatsApp credential fetched successfully",
            "data": serialize_whatsapp_credential(credential),
        }

    return await db.run_sync(sync_op)


@whatsapp_router.post("/whatsapp-template/register")
async def register_whatsapp_template(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(lambda sync_db: template_service.register_template(sync_db, body))


@whatsapp_router.get("/whatsapp-template/get-list")
async def get_whatsapp_template_list(
    name: str = "",
    language: str = "",
    category: str = "",
    status: str = "",
    authentication: bool = False,
    offset: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: template_service.list_templates(
            sync_db,
            name=name,
            language=language,
            category=category,
            status=status,
            authentication=authentication,
            offset=offset,
            limit=limit,
        )
    )


@whatsapp_router.get("/whatsapp-template/byid/{template_id}")
async def get_whatsapp_template_by_id(template_id: int, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: template_service.get_template_by_id(sync_db, template_id))


@whatsapp_router.put("/whatsapp-template/update/{template_id}")
async def update_whatsapp_template(template_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: template_service.update_template(sync_db, template_id, body)
    )


@whatsapp_router.delete("/whatsapp-template/delete/{template_id}")
async def delete_whatsapp_template(template_id: int, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: template_service.delete_template(sync_db, template_id))


@whatsapp_router.get("/whatsapp-template/sync-template")
async def sync_whatsapp_templates(db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: template_service.sync_templates(sync_db))


@whatsapp_router.get("/whatsapp-template/get-status/{template_id}")
async def get_whatsapp_template_status(template_id: int, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: template_service.get_template_status(sync_db, template_id))


@whatsapp_router.get("/whatsapp-template/preview")
async def preview_whatsapp_template(
    languages: str | None = None,
    add_security_recommendation: bool = False,
    code_expiration_minutes: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: template_service.auth_template_preview(
            sync_db,
            languages=languages,
            add_security_recommendation=add_security_recommendation,
            code_expiration_minutes=code_expiration_minutes,
        )
    )


@whatsapp_router.get("/whatsapp-template/language")
async def get_whatsapp_template_languages(db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: template_service.list_languages(sync_db))


@whatsapp_router.get("/whatsapp-message/contacts/get")
async def get_live_chat_contacts(
    offset: int = 0,
    limit: int = 15,
    searchValue: str = "",
    status: str | None = None,
    tags: str | None = None,
    tag_ids: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: list_chat_contacts(
            sync_db,
            offset=offset,
            limit=limit,
            search_value=searchValue,
            status=status,
            tags=tags,
            tag_ids=tag_ids,
        )
    )


@whatsapp_router.post("/whatsapp-message/contacts/add")
async def add_or_update_contact(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: upsert_manual_contact(
            sync_db,
            customer_phone_number=str(body.get("customer_phone_number") or ""),
            custom_name=str(body.get("custom_name") or ""),
            remark=body.get("remark"),
        )
    )


@whatsapp_router.patch("/whatsapp-message/contacts/update-status")
async def update_whatsapp_contact_status(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: update_contact_status(
            sync_db,
            customer_phone_number=str(body.get("customer_phone_number") or ""),
            status=str(body.get("status") or ""),
        )
    )


@whatsapp_router.delete("/whatsapp-message/contacts/delete")
async def delete_whatsapp_contact(contact: str, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(
        lambda sync_db: delete_contact(sync_db, customer_phone_number=contact)
    )


@whatsapp_router.get("/whatsapp-message/tags/get-list")
async def get_whatsapp_tags(
    search: str = "",
    offset: int = 0,
    limit: int = 20,
    status: str = "true",
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: list_tags(
            sync_db,
            search=search,
            offset=offset,
            limit=limit,
            status=status,
        )
    )


@whatsapp_router.post("/whatsapp-message/tags/create")
async def create_whatsapp_tag(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: create_tag(
            sync_db,
            name=str(body.get("name") or ""),
            color=body.get("color"),
            description=body.get("description"),
        )
    )


@whatsapp_router.post("/whatsapp-message/tags/assign")
async def assign_whatsapp_tags(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    tag_ids = [int(tag_id) for tag_id in body.get("tag_ids") or []]
    return await db.run_sync(
        lambda sync_db: assign_tags_to_contact(
            sync_db,
            contact_id=int(body.get("contact_id") or 0),
            tag_ids=tag_ids,
        )
    )


@whatsapp_router.post("/whatsapp-message/tags/remove")
async def remove_whatsapp_tag(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: remove_tag_from_contact(
            sync_db,
            contact_id=int(body.get("contact_id") or 0),
            tag_id=int(body.get("tag_id") or 0),
        )
    )


@whatsapp_router.get("/whatsapp-message/chat")
async def get_live_chat_messages(contact: str, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: get_chat_messages(sync_db, contact))


@whatsapp_router.post("/whatsapp-message/send-media")
async def send_live_chat_message(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        data = await request.json()
    except Exception:
        form = await request.form()
        data = dict(form)
    to_no = str(data.get("to_no") or "")
    message_body = str(data.get("message_body") or data.get("message") or "")
    if not to_no or not message_body:
        raise HTTPException(status_code=400, detail="to_no and message_body are required")

    try:
        result = await db.run_sync(
            lambda sync_db: send_live_chat_text(
                sync_db,
                to_no=to_no,
                message_body=message_body,
            )
        )
        await live_chat_manager.broadcast(
            {
                "type": "live_chat_message",
                "direction": "out",
                "contact": to_no,
                "message": result.get("data"),
            }
        )
        return result
    except requests.RequestException as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@whatsapp_router.post("/whatsapp-message/send-template")
async def send_live_chat_template(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    result = await db.run_sync(
        lambda sync_db: template_service.send_template_message(
            sync_db,
            to_no=str(body.get("to_no") or ""),
            template_id=int(body.get("template_id") or 0),
            variables=body.get("variables") or {},
        )
    )
    if result.get("success"):
        await live_chat_manager.broadcast(
            {
                "type": "live_chat_message",
                "direction": "out",
                "contact": body.get("to_no"),
                "message": result.get("data"),
            }
        )
    return result


@whatsapp_router.post("/whatsapp-message/mark-read")
@whatsapp_router.post("/whatsapp-message/read-with-typing")
async def mark_live_chat_message_read(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        body = {}
    message_id = body.get("message_id") or body.get("msg_id")
    contact = body.get("customer_phone_number") or body.get("contact")
    return await db.run_sync(
        lambda sync_db: mark_chat_read(
            sync_db,
            message_id=str(message_id) if message_id else None,
            contact=str(contact) if contact else None,
        )
    )


async def process_webhook_event_background(event_id: int) -> None:
    await asyncio.to_thread(_process_webhook_event_with_session, event_id)


def _process_webhook_event_sync(sync_db, event_id: int):
    event = sync_db.execute(
        select(WebhookEvent).where(WebhookEvent.id == event_id)
    ).scalars().first()
    if not event:
        return None
    import asyncio

    return asyncio.run(process_webhook_event(event, sync_db))


def _process_webhook_event_with_session(event_id: int) -> None:
    with SessionLocal() as sync_db:
        event = sync_db.execute(
            select(WebhookEvent).where(WebhookEvent.id == event_id)
        ).scalars().first()
        if not event:
            return
        try:
            _process_webhook_event_sync(sync_db, event_id)
        except Exception as exc:
            mark_webhook_event_failed(sync_db, event, exc)


@whatsapp_router.post("/send-message")
async def send_message(
    data: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        response = await run_in_threadpool(send_whatsapp_message, data.phone, data.message)
        await db.run_sync(lambda sync_db: save_message(sync_db, data.phone, data.message, "outgoing"))
        return {"status": "sent", "whatsapp": response}
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@whatsapp_router.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    verify_token = settings.verify_token

    if mode == "subscribe" and token == verify_token and challenge:
        return PlainTextResponse(content=challenge)

    return PlainTextResponse(content="Verification failed", status_code=403)


@whatsapp_router.get("/whatsapp/cloud-api/callback")
async def verify_whatsapp_cloud_callback(request: Request):
    return await verify_webhook(request)


@whatsapp_router.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "invalid_json"}

    incoming_messages = parse_whatsapp_messages(body)
    queued = 0
    skipped = 0

    for incoming in incoming_messages:
        event, created = await db.run_sync(
            lambda sync_db: get_or_create_webhook_event(sync_db, incoming)
        )
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


@whatsapp_router.post("/whatsapp/cloud-api/callback")
async def receive_whatsapp_cloud_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    return await receive_webhook(request, background_tasks, db)


@whatsapp_router.get("/webhook/events")
async def list_webhook_events(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    statement = select(WebhookEvent)
    if status:
        statement = statement.where(WebhookEvent.status == status)
    result = await db.execute(statement.order_by(WebhookEvent.created_at.desc()).limit(200))
    rows = result.scalars().all()
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


@whatsapp_router.post("/webhook/events/retry-failed")
async def retry_failed_webhook_events(
    data: RetryWebhookEventsRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WebhookEvent)
        .where(WebhookEvent.status == "failed")
        .order_by(WebhookEvent.created_at.asc())
        .limit(max(1, min(data.limit, 100)))
    )
    rows = result.scalars().all()

    retried = 0
    failed = 0
    errors = []
    for event in rows:
        try:
            await asyncio.to_thread(_process_webhook_event_with_session, event.id)
            retried += 1
        except Exception as exc:
            await db.run_sync(lambda sync_db: mark_webhook_event_failed(sync_db, sync_db.merge(event), exc))
            failed += 1
            errors.append({"event_id": event.id, "error": str(exc)})

    return {
        "status": "completed",
        "retried": retried,
        "failed": failed,
        "errors": errors[:5],
    }


@whatsapp_router.get("/conversations")
async def get_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message.phone).distinct())
    return [{"phone": phone} for phone in result.scalars().all()]


@whatsapp_router.get("/messages/{phone}")
async def get_messages(phone: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message)
        .where(Message.phone == phone)
        .order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
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
