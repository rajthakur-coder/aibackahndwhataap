import requests
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.session import AsyncSessionLocal, get_db
from app.models import User
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
from app.shared.tenant import (
    current_tenant_id,
    normalize_tenant_id,
    reset_current_tenant_id,
    set_current_tenant_id,
)
from app.utils import decode_token

router = APIRouter()
websocket_router = APIRouter()

@websocket_router.websocket("/ws/live-chat")
async def live_chat_websocket(websocket: WebSocket):
    tenant_id = await _tenant_id_from_websocket(websocket)
    if not tenant_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    tenant_token = set_current_tenant_id(tenant_id)
    await live_chat_manager.connect(websocket, tenant_id=tenant_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        live_chat_manager.disconnect(websocket, tenant_id=tenant_id)
    except Exception:
        live_chat_manager.disconnect(websocket, tenant_id=tenant_id)
    finally:
        reset_current_tenant_id(tenant_token)


async def _tenant_id_from_websocket(websocket: WebSocket) -> str | None:
    token = websocket.cookies.get("access_token")
    authorization = websocket.headers.get("authorization")
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
    if not token:
        return None

    payload = decode_token(token)
    if payload is None or not payload.get("id"):
        return None
    async with AsyncSessionLocal() as db:
        user = await db.get(User, payload["id"])
        if user is None:
            return None
        return normalize_tenant_id(str(user.id))


def _current_request_tenant_id() -> str:
    tenant_id = current_tenant_id()
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated (No tenant found)")
    return normalize_tenant_id(tenant_id)

@router.get("/whatsapp-message/contacts/get")
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

@router.post("/whatsapp-message/contacts/add")
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

@router.patch("/whatsapp-message/contacts/update-status")
async def update_whatsapp_contact_status(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: update_contact_status(
            sync_db,
            customer_phone_number=str(body.get("customer_phone_number") or ""),
            status=str(body.get("status") or ""),
        )
    )

@router.delete("/whatsapp-message/contacts/delete")
async def delete_whatsapp_contact(contact: str, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(
        lambda sync_db: delete_contact(sync_db, customer_phone_number=contact)
    )

@router.get("/whatsapp-message/tags/get-list")
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

@router.post("/whatsapp-message/tags/create")
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

@router.post("/whatsapp-message/tags/assign")
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

@router.post("/whatsapp-message/tags/remove")
async def remove_whatsapp_tag(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    return await db.run_sync(
        lambda sync_db: remove_tag_from_contact(
            sync_db,
            contact_id=int(body.get("contact_id") or 0),
            tag_id=int(body.get("tag_id") or 0),
        )
    )

@router.get("/whatsapp-message/chat")
async def get_live_chat_messages(contact: str, db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: get_chat_messages(sync_db, contact))

@router.post("/whatsapp-message/send-media")
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
            },
            tenant_id=_current_request_tenant_id(),
        )
        return result
    except requests.RequestException as exc:
        detail = exc.response.text if exc.response is not None else str(exc)
        raise HTTPException(status_code=502, detail=detail) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/whatsapp-message/send-template")
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
            },
            tenant_id=_current_request_tenant_id(),
        )
    return result

@router.post("/whatsapp-message/mark-read")
@router.post("/whatsapp-message/read-with-typing")
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

