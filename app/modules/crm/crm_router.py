import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.crm import (
    AgentAction,
    Appointment,
    CustomerMemory,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.modules.crm.crm_schema import ActionRequest, OrderRequest


crm_router = APIRouter(tags=["crm"])


def json_dumps(value: dict | None) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


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


@crm_router.get("/handoffs")
async def list_handoffs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(HandoffTicket).order_by(HandoffTicket.created_at.desc()))
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "reason": row.reason,
            "status": row.status,
            "summary": row.summary,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]


@crm_router.post("/handoffs/{ticket_id}/close")
async def close_handoff(ticket_id: int, db: AsyncSession = Depends(get_db)):
    ticket = await db.get(HandoffTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "closed"
    await db.commit()
    return {"status": "success", "ticket_id": ticket.id}


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
async def list_agent_actions(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentAction).order_by(AgentAction.created_at.desc()).limit(100)
    )
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "action_type": row.action_type,
            "status": row.status,
            "payload": row.payload,
            "result": row.result,
            "created_at": str(row.created_at),
        }
        for row in rows
    ]
