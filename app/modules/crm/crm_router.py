import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
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


crm_router = APIRouter(tags=["crm"])


def json_dumps(value: dict | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


def _db_bool(value: bool) -> str:
    return "true" if value else "false"


def _is_db_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_or_create_bot_settings_sync(db) -> BotSettings:
    row = db.execute(select(BotSettings).where(BotSettings.tenant_id == "default")).scalars().first()
    if row:
        return row
    row = BotSettings(
        tenant_id="default",
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
        "default_language": row.default_language or "auto",
        "welcome_message": row.welcome_message or "",
        "fallback_message": row.fallback_message or "",
        "offline_message": row.offline_message or "",
        "ai_personality": row.ai_personality or "helpful",
        "ai_tone": row.ai_tone or "friendly",
        "response_length": row.response_length or "brief",
        "custom_instructions": row.custom_instructions or "",
        "main_menu_buttons": main_menu_buttons if isinstance(main_menu_buttons, list) else [],
        "handoff_keywords": handoff_keywords if isinstance(handoff_keywords, list) else [],
        "business_hours_enabled": _is_db_true(row.business_hours_enabled),
        "business_hours_start": row.business_hours_start or "09:00",
        "business_hours_end": row.business_hours_end or "18:00",
        "timezone": row.timezone or "Asia/Kolkata",
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


@crm_router.get("/bot/settings")
async def get_bot_settings(db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: serialize_bot_settings(_get_or_create_bot_settings_sync(sync_db)))


@crm_router.put("/bot/settings")
async def update_bot_settings(data: BotSettingsRequest, db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db):
        row = _get_or_create_bot_settings_sync(sync_db)
        row.bot_enabled = _db_bool(data.bot_enabled)
        row.default_language = data.default_language.strip() or "auto"
        row.welcome_message = (data.welcome_message or "").strip() or row.welcome_message
        row.fallback_message = (data.fallback_message or "").strip() or row.fallback_message
        row.offline_message = (data.offline_message or "").strip() or row.offline_message
        row.ai_personality = data.ai_personality.strip() or "helpful"
        row.ai_tone = data.ai_tone.strip() or "friendly"
        row.response_length = data.response_length.strip() or "brief"
        row.custom_instructions = (data.custom_instructions or "").strip()[:2000]
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
        return serialize_bot_settings(row)

    return {"status": "success", "settings": await db.run_sync(sync_op)}


@crm_router.get("/leads")
async def list_leads(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Lead).order_by(Lead.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "name": row.name,
            "email": row.email,
            "intent": row.intent,
            "status": row.status,
            "source": row.source,
            "notes": row.notes,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@crm_router.get("/appointments")
async def list_appointments(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Appointment).order_by(Appointment.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "customer_name": row.customer_name,
            "requested_time": row.requested_time,
            "status": row.status,
            "notes": row.notes,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@crm_router.post("/orders")
async def upsert_order(data: OrderRequest, db: AsyncSession = Depends(get_db)):
    order_id = data.order_id.strip().upper()
    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID is required")

    result = await db.execute(select(OrderStatus).where(OrderStatus.order_id == order_id))
    row = result.scalars().first()
    if not row:
        row = OrderStatus(order_id=order_id)
        db.add(row)

    row.phone = data.phone or row.phone
    row.status = data.status
    row.details = data.details
    await db.commit()
    await db.refresh(row)

    return {
        "status": "success",
        "id": row.id,
        "order_id": row.order_id,
        "order_status": row.status,
    }


@crm_router.get("/orders")
async def list_orders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OrderStatus).order_by(OrderStatus.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "order_id": row.order_id,
            "status": row.status,
            "details": row.details,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


def serialize_handoff(ticket: HandoffTicket) -> dict:
    return {
        "id": ticket.id,
        "phone": ticket.phone,
        "reason": ticket.reason,
        "status": ticket.status,
        "summary": ticket.summary,
        "created_at": str(ticket.created_at),
        "updated_at": str(ticket.updated_at),
    }


@crm_router.get("/handoffs")
async def list_handoffs(status: str | None = None, db: AsyncSession = Depends(get_db)):
    statement = select(HandoffTicket)
    if status:
        statement = statement.where(HandoffTicket.status == status.strip())
    result = await db.execute(statement.order_by(HandoffTicket.created_at.desc()))
    rows = result.scalars().all()
    return [serialize_handoff(row) for row in rows]


@crm_router.post("/handoffs/{ticket_id}/close")
async def close_handoff(ticket_id: int, db: AsyncSession = Depends(get_db)):
    return await resolve_handoff(ticket_id, HandoffResolveRequest(), db)


@crm_router.post("/handoffs/{ticket_id}/resolve")
async def resolve_handoff(
    ticket_id: int,
    data: HandoffResolveRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    ticket = await db.get(HandoffTicket, ticket_id)
    if not ticket:
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
            payload=json_dumps({"ticket_id": ticket.id, "note": data.note if data else None}),
            result=json_dumps({"bot_resumed": True}),
        )
    )
    await db.commit()
    await db.refresh(ticket)
    return {"status": "success", "ticket": serialize_handoff(ticket), "bot_resumed": True}


@crm_router.post("/handoffs/{ticket_id}/reopen")
async def reopen_handoff(ticket_id: int, db: AsyncSession = Depends(get_db)):
    ticket = await db.get(HandoffTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "open"
    ticket.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(ticket)
    return {"status": "success", "ticket": serialize_handoff(ticket), "bot_paused": True}


@crm_router.get("/customers/{phone}/memory")
async def get_customer_memory(phone: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CustomerMemory)
        .where(CustomerMemory.phone == phone)
        .order_by(CustomerMemory.created_at.desc())
    )
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "memory_type": row.memory_type,
            "content": row.content,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@crm_router.post("/agent/actions/crm-update")
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


@crm_router.post("/agent/actions/email")
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


@crm_router.post("/agent/actions/payment-link")
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


@crm_router.get("/agent/actions")
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
