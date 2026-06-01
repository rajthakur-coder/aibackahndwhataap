import requests
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.models.whatsapp import Message
from app.modules.whatsapp.live_chat.live_chat_service import (
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
from app.modules.whatsapp.live_chat.socket import live_chat_manager
from app.modules.whatsapp.templates import template_service
from app.modules.whatsapp.whatsapp_schema import (
    SendMessageRequest,
    WhatsappNumberSetupRequest,
)
from app.modules.whatsapp.whatsapp_service import (
    get_whatsapp_credential,
    save_message,
    serialize_whatsapp_credential,
    send_whatsapp_message,
    setup_whatsapp_business,
)
from app.shared.tenant import tenant_id_from_header
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id

router = APIRouter()

@router.post("/send-message")
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

@router.get("/conversations")
async def get_conversations(db: AsyncSession = Depends(get_db)):
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    result = await db.execute(select(Message.phone).where(Message.tenant_id == tenant_id).distinct())
    return [{"phone": phone} for phone in result.scalars().all()]

@router.get("/messages/{phone}")
async def get_messages(phone: str, db: AsyncSession = Depends(get_db)):
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    result = await db.execute(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.phone == phone)
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

