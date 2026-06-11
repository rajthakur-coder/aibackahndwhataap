import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.shared.tenant import strict_tenant_id
from app.models.crm import (
    AgentAction,
    Appointment,
    BotSettings,
    CustomerMemory,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.models.whatsapp import Message
from app.modules.crm.crm_schema import ActionRequest, BotSettingsRequest, HandoffResolveRequest, OrderRequest
from app.modules.crm.agent.agent_service import clear_bot_settings_cache
from app.modules.whatsapp.live_chat.contact_service import serialize_message

router = APIRouter()

def serialize_handoff(ticket: HandoffTicket, messages: list[Message] | None = None) -> dict:
    return {
        "id": ticket.id,
        "tenant_id": ticket.tenant_id,
        "phone": ticket.phone,
        "reason": ticket.reason,
        "status": ticket.status,
        "summary": ticket.summary,
        "messages": [serialize_message(message) for message in messages or []],
        "created_at": str(ticket.created_at),
        "updated_at": str(ticket.updated_at),
    }

@router.get("/handoffs")
async def list_handoffs(
    status: str | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    statement = select(HandoffTicket).where(HandoffTicket.tenant_id == tenant_id)
    if status:
        statement = statement.where(HandoffTicket.status == status.strip())
    result = await db.execute(statement.order_by(HandoffTicket.created_at.desc()))
    rows = result.scalars().all()
    serialized = []
    for row in rows:
        messages_result = await db.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.phone == row.phone)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(80)
        )
        serialized.append(serialize_handoff(row, messages_result.scalars().all()))
    return serialized

@router.post("/handoffs/{ticket_id}/close")
async def close_handoff(
    ticket_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    return await resolve_handoff(ticket_id, HandoffResolveRequest(), tenant_id, db)

@router.post("/handoffs/{ticket_id}/resolve")
async def resolve_handoff(
    ticket_id: int,
    data: HandoffResolveRequest | None = None,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(HandoffTicket, ticket_id)
    if not ticket or ticket.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "closed"
    ticket.updated_at = datetime.utcnow()
    if data and data.note:
        line = f"resolved: {data.note.strip()}"
        ticket.summary = "\n".join(filter(None, [ticket.summary, line]))[-5000:]
    db.add(
        AgentAction(
            phone=ticket.phone,
            action_type="handoff_resolved",
            status="closed",
            payload=json.dumps({"ticket_id": ticket.id, "note": data.note if data else None}),
            result=json.dumps({"bot_resumed": True}),
        )
    )
    await db.commit()
    await db.refresh(ticket)
    return {"status": "success", "ticket": serialize_handoff(ticket), "bot_resumed": True}

@router.post("/handoffs/{ticket_id}/reopen")
async def reopen_handoff(
    ticket_id: int,
    tenant_id: str = Depends(strict_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(HandoffTicket, ticket_id)
    if not ticket or ticket.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "open"
    ticket.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(ticket)
    return {"status": "success", "ticket": serialize_handoff(ticket), "bot_paused": True}
