from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.entities import (
    AgentAction,
    Appointment,
    CustomerMemory,
    HandoffTicket,
    Lead,
    OrderStatus,
)
from app.schemas import ActionRequest, OrderRequest
from app.services.agent import log_crm_update, log_email_request, log_payment_link_request


router = APIRouter(tags=["crm"])


@router.get("/leads")
def list_leads(db: Session = Depends(get_db)):
    rows = db.query(Lead).order_by(Lead.created_at.desc()).all()
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
def list_appointments(db: Session = Depends(get_db)):
    rows = db.query(Appointment).order_by(Appointment.created_at.desc()).all()
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
def upsert_order(data: OrderRequest, db: Session = Depends(get_db)):
    order_id = data.order_id.strip().upper()
    if not order_id:
        raise HTTPException(status_code=400, detail="Order ID is required")

    row = db.query(OrderStatus).filter(OrderStatus.order_id == order_id).first()
    if not row:
        row = OrderStatus(order_id=order_id)
        db.add(row)

    row.phone = data.phone or row.phone
    row.status = data.status
    row.details = data.details
    db.commit()
    db.refresh(row)

    return {
        "status": "success",
        "id": row.id,
        "order_id": row.order_id,
        "order_status": row.status,
    }


@router.get("/orders")
def list_orders(db: Session = Depends(get_db)):
    rows = db.query(OrderStatus).order_by(OrderStatus.created_at.desc()).all()
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


@router.get("/handoffs")
def list_handoffs(db: Session = Depends(get_db)):
    rows = db.query(HandoffTicket).order_by(HandoffTicket.created_at.desc()).all()
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


@router.post("/handoffs/{ticket_id}/close")
def close_handoff(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(HandoffTicket).filter(HandoffTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Handoff ticket not found")
    ticket.status = "closed"
    db.commit()
    return {"status": "success", "ticket_id": ticket.id}


@router.get("/customers/{phone}/memory")
def get_customer_memory(phone: str, db: Session = Depends(get_db)):
    rows = (
        db.query(CustomerMemory)
        .filter(CustomerMemory.phone == phone)
        .order_by(CustomerMemory.created_at.desc())
        .all()
    )
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


@router.post("/agent/actions/crm-update")
def crm_update(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_crm_update(db, data.phone, data.payload)
    return {"status": "logged", "action_id": action.id}


@router.post("/agent/actions/email")
def email_action(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_email_request(db, data.phone, data.payload)
    return {"status": "queued", "action_id": action.id}


@router.post("/agent/actions/payment-link")
def payment_link_action(data: ActionRequest, db: Session = Depends(get_db)):
    action = log_payment_link_request(db, data.phone, data.payload)
    return {"status": "logged", "action_id": action.id}


@router.get("/agent/actions")
def list_agent_actions(db: Session = Depends(get_db)):
    rows = db.query(AgentAction).order_by(AgentAction.created_at.desc()).limit(100).all()
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
