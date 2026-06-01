import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.audit import write_async_audit_log
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id, strict_tenant_id
from app.models.crm import (
    AgentAction,
    Appointment,
    BotSettings,
    CustomerMemory,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.modules.crm.crm_schema import ActionRequest, BotSettingsRequest, HandoffResolveRequest, OrderRequest
from app.modules.crm.agent.agent_service import clear_bot_settings_cache

router = APIRouter()

def json_dumps(value: dict | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True)

def _db_bool(value: bool) -> str:
    return "true" if value else "false"

def _is_db_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

def _get_or_create_bot_settings_sync(db, tenant_id: str = DEFAULT_TENANT_ID) -> BotSettings:
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(select(BotSettings).where(BotSettings.tenant_id == tenant_id)).scalars().first()
    if row:
        if not row.default_language:
            row.default_language = "english"
            db.commit()
            db.refresh(row)
        return row
    row = BotSettings(
        tenant_id=tenant_id,
        default_language="english",
        handoff_keywords=json.dumps(["human", "agent", "support", "complaint", "manager"], ensure_ascii=True),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def serialize_bot_settings(row: BotSettings) -> dict:
    try:
        handoff_keywords = json.loads(row.handoff_keywords or "[]")
    except json.JSONDecodeError:
        handoff_keywords = []
    try:
        main_menu_buttons = json.loads(row.main_menu_buttons or "[]")
    except json.JSONDecodeError:
        main_menu_buttons = []
    return {
        "bot_enabled": _is_db_true(row.bot_enabled),
        "default_language": row.default_language or "english",
        "welcome_message": row.welcome_message or "",
        "fallback_message": row.fallback_message or "",
        "offline_message": row.offline_message or "",
        "ai_personality": row.ai_personality or "helpful",
        "ai_tone": row.ai_tone or "friendly",
        "response_length": row.response_length or "brief",
        "custom_instructions": row.custom_instructions or "",
        "brand_prompt": row.brand_prompt or "",
        "main_menu_buttons": main_menu_buttons if isinstance(main_menu_buttons, list) else [],
        "handoff_keywords": handoff_keywords if isinstance(handoff_keywords, list) else [],
        "business_hours_enabled": _is_db_true(row.business_hours_enabled),
        "business_hours_start": row.business_hours_start or "09:00",
        "business_hours_end": row.business_hours_end or "18:00",
        "timezone": row.timezone or "Asia/Kolkata",
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }

@router.get("/bot/settings")
async def get_bot_settings(
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await db.run_sync(lambda sync_db: serialize_bot_settings(_get_or_create_bot_settings_sync(sync_db, tenant_id)))

@router.put("/bot/settings")
async def update_bot_settings(
    data: BotSettingsRequest,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    def sync_op(sync_db):
        row = _get_or_create_bot_settings_sync(sync_db, tenant_id)
        row.bot_enabled = _db_bool(data.bot_enabled)
        row.default_language = data.default_language.strip() or "english"
        row.welcome_message = (data.welcome_message or "").strip() or row.welcome_message
        row.fallback_message = (data.fallback_message or "").strip() or row.fallback_message
        row.offline_message = (data.offline_message or "").strip() or row.offline_message
        row.ai_personality = data.ai_personality.strip() or "helpful"
        row.ai_tone = data.ai_tone.strip() or "friendly"
        row.response_length = data.response_length.strip() or "brief"
        row.custom_instructions = (data.custom_instructions or "").strip()[:2000]
        row.brand_prompt = (data.brand_prompt or "").strip()
        row.main_menu_buttons = json.dumps(
            [
                {
                    "id": str(button.get("id") or "").strip(),
                    "title": str(button.get("title") or "").strip()[:20],
                }
                for button in data.main_menu_buttons
                if str(button.get("id") or "").strip() and str(button.get("title") or "").strip()
            ][:3],
            ensure_ascii=True,
        )
        row.handoff_keywords = json.dumps(
            [keyword.strip().lower() for keyword in data.handoff_keywords if keyword.strip()],
            ensure_ascii=True,
        )
        row.business_hours_enabled = _db_bool(data.business_hours_enabled)
        row.business_hours_start = data.business_hours_start.strip() or "09:00"
        row.business_hours_end = data.business_hours_end.strip() or "18:00"
        row.timezone = data.timezone.strip() or "Asia/Kolkata"
        sync_db.commit()
        sync_db.refresh(row)
        clear_bot_settings_cache(tenant_id)
        return serialize_bot_settings(row)

    settings = await db.run_sync(sync_op)
    await write_async_audit_log(
        db,
        action="bot_settings.updated",
        tenant_id=tenant_id,
        entity_type="bot_settings",
        entity_id=tenant_id,
        metadata={
            "bot_enabled": settings["bot_enabled"],
            "default_language": settings["default_language"],
            "business_hours_enabled": settings["business_hours_enabled"],
        },
        commit=True,
    )
    return {"status": "success", "settings": settings}
