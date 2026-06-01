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
from app.shared.tenant import strict_tenant_id

router = APIRouter()

@router.post("/whatsapp-credential/number-setup")
async def setup_whatsapp_number(
    data: WhatsappNumberSetupRequest,
    tenant_id: str = Depends(strict_tenant_id),
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
                tenant_id=tenant_id,
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

@router.get("/whatsapp-credential/get")
async def get_my_whatsapp_credential(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db):
        credential = get_whatsapp_credential(sync_db, tenant_id=tenant_id)
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

