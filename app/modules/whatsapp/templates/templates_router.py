import requests
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.modules.audit import write_async_audit_log
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
from app.modules.compliance.template_compliance import check_template_compliance
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

@router.post("/whatsapp-template/register")
async def register_whatsapp_template(
    request: Request,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    compliance = check_template_compliance(body)
    if not compliance["ok"]:
        raise HTTPException(status_code=400, detail={"message": "Template failed compliance checks", "issues": compliance["issues"]})
    result = await db.run_sync(lambda sync_db: template_service.register_template(sync_db, body, tenant_id=tenant_id))
    await write_async_audit_log(
        db,
        action="whatsapp_template.created",
        tenant_id=tenant_id,
        entity_type="whatsapp_template",
        entity_id=(result.get("data") or {}).get("id") if isinstance(result, dict) else None,
        metadata={"name": body.get("name"), "language": body.get("language"), "category": body.get("category")},
        commit=True,
    )
    return result

@router.get("/whatsapp-template/get-list")
async def get_whatsapp_template_list(
    name: str = "",
    language: str = "",
    category: str = "",
    status: str = "",
    authentication: bool = False,
    offset: int = 0,
    limit: int = 20,
    tenant_id: str = Depends(strict_tenant_id),
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
            tenant_id=tenant_id,
        )
    )

@router.get("/whatsapp-template/byid/{template_id}")
async def get_whatsapp_template_by_id(
    template_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: template_service.get_template_by_id(sync_db, template_id, tenant_id=tenant_id))

@router.put("/whatsapp-template/update/{template_id}")
async def update_whatsapp_template(
    template_id: int,
    request: Request,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    body = await request.json()
    compliance = check_template_compliance(body)
    if not compliance["ok"]:
        raise HTTPException(status_code=400, detail={"message": "Template failed compliance checks", "issues": compliance["issues"]})
    result = await db.run_sync(
        lambda sync_db: template_service.update_template(sync_db, template_id, body, tenant_id=tenant_id)
    )
    await write_async_audit_log(
        db,
        action="whatsapp_template.updated",
        tenant_id=tenant_id,
        entity_type="whatsapp_template",
        entity_id=template_id,
        metadata={"fields": sorted(body.keys()) if isinstance(body, dict) else []},
        commit=True,
    )
    return result

@router.delete("/whatsapp-template/delete/{template_id}")
async def delete_whatsapp_template(
    template_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.run_sync(lambda sync_db: template_service.delete_template(sync_db, template_id, tenant_id=tenant_id))
    await write_async_audit_log(
        db,
        action="whatsapp_template.deleted",
        tenant_id=tenant_id,
        entity_type="whatsapp_template",
        entity_id=template_id,
        commit=True,
    )
    return result

@router.get("/whatsapp-template/sync-template")
async def sync_whatsapp_templates(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.run_sync(lambda sync_db: template_service.sync_templates(sync_db, tenant_id=tenant_id))
    await write_async_audit_log(
        db,
        action="whatsapp_template.synced",
        tenant_id=tenant_id,
        entity_type="whatsapp_template",
        metadata={"synced_count": (result.get("data") or {}).get("synced_count") if isinstance(result, dict) else None},
        commit=True,
    )
    return result

@router.get("/whatsapp-template/get-status/{template_id}")
async def get_whatsapp_template_status(
    template_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: template_service.get_template_status(sync_db, template_id, tenant_id=tenant_id))

@router.get("/whatsapp-template/preview")
async def preview_whatsapp_template(
    languages: str | None = None,
    add_security_recommendation: bool = False,
    code_expiration_minutes: int | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(
        lambda sync_db: template_service.auth_template_preview(
            sync_db,
            languages=languages,
            add_security_recommendation=add_security_recommendation,
            code_expiration_minutes=code_expiration_minutes,
            tenant_id=tenant_id,
        )
    )

@router.get("/whatsapp-template/language")
async def get_whatsapp_template_languages(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: template_service.list_languages(sync_db, tenant_id=tenant_id))

