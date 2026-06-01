import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id, tenant_id_from_header
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

@router.post("/agent/actions/crm-update")
async def crm_update(data: ActionRequest, db: AsyncSession = Depends(get_db)):
    action = AgentAction(
        phone=data.phone,
        action_type="crm_update",
        status="logged",
        payload=json_dumps(data.payload),
        result=json_dumps({}),
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)
    return {"status": "logged", "action_id": action.id}

@router.post("/agent/actions/email")
async def email_action(data: ActionRequest, db: AsyncSession = Depends(get_db)):
    action = AgentAction(
        phone=data.phone,
        action_type="email_send",
        status="queued",
        payload=json_dumps(data.payload),
        result=json_dumps({}),
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)
    return {"status": "queued", "action_id": action.id}

@router.post("/agent/actions/payment-link")
async def payment_link_action(data: ActionRequest, db: AsyncSession = Depends(get_db)):
    action = AgentAction(
        phone=data.phone,
        action_type="payment_link",
        status="logged",
        payload=json_dumps(data.payload),
        result=json_dumps({"status": "configure_gateway_to_send_real_links"}),
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)
    return {"status": "logged", "action_id": action.id}

@router.get("/agent/actions")
async def list_agent_actions(
    limit: int = 10,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    total = await db.scalar(select(func.count()).select_from(AgentAction))
    result = await db.execute(
        select(AgentAction)
        .order_by(AgentAction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = result.scalars().all()
    return {
        "items": [
            {
                "sr_no": offset + index,
                "id": row.id,
                "phone": row.phone,
                "action_type": row.action_type,
                "status": row.status,
                "payload": row.payload,
                "result": row.result,
                "created_at": str(row.created_at),
            }
            for index, row in enumerate(rows, start=1)
        ],
        "total": total or 0,
        "limit": limit,
        "offset": offset,
    }
