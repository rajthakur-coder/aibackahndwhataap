import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.shared.tenant import normalize_tenant_id, strict_tenant_id, tenant_id_from_header
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

@router.get("/leads")
async def list_leads(
    tenant_id: str = Depends(tenant_id_from_header),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = normalize_tenant_id(tenant_id)
    result = await db.execute(select(Lead).where(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc()))
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

@router.get("/appointments")
async def list_appointments(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Appointment).where(Appointment.tenant_id == tenant_id).order_by(Appointment.created_at.desc()))
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

@router.post("/orders")
async def upsert_order(data: OrderRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    order_id = data.order_id.strip().upper()
    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID is required")

    result = await db.execute(select(OrderStatus).where(OrderStatus.tenant_id == tenant_id, OrderStatus.order_id == order_id))
    row = result.scalars().first()
    if not row:
        row = OrderStatus(tenant_id=tenant_id, order_id=order_id)
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

@router.get("/orders")
async def list_orders(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OrderStatus).where(OrderStatus.tenant_id == tenant_id).order_by(OrderStatus.created_at.desc()))
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

@router.get("/customers/{phone}/memory")
async def get_customer_memory(phone: str, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CustomerMemory)
        .where(CustomerMemory.tenant_id == tenant_id, CustomerMemory.phone == phone)
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
