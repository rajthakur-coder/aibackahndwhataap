from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.compliance import CSATResponse, CustomerConsent, DataPrincipalRequestLog
from app.models.crm import Lead
from app.models.ecommerce import EcommerceCart, EcommerceOrder, EcommerceReturnRequest
from app.models.whatsapp import Message, WebhookEvent, WhatsappInteractionEvent
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def capture_consent(db: Session, tenant_id: str, phone: str, consent_type: str, status: str, purpose: str | None = None) -> dict:
    row = CustomerConsent(
        tenant_id=normalize_tenant_id(tenant_id),
        phone=phone,
        consent_type=consent_type,
        status=status,
        purpose=purpose,
        revoked_at=datetime.utcnow() if status == "revoked" else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "phone": row.phone, "consent_type": row.consent_type, "status": row.status}


def latest_consent_status(db: Session, tenant_id: str, phone: str, consent_type: str = "marketing") -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(
        select(CustomerConsent)
        .where(
            CustomerConsent.tenant_id == tenant_id,
            CustomerConsent.phone == phone,
            CustomerConsent.consent_type == consent_type,
        )
        .order_by(CustomerConsent.created_at.desc())
        .limit(1)
    ).scalars().first()
    if not row:
        return {"tenant_id": tenant_id, "phone": phone, "consent_type": consent_type, "status": "unknown", "allowed": False}
    return {
        "tenant_id": tenant_id,
        "phone": phone,
        "consent_type": consent_type,
        "status": row.status,
        "allowed": row.status == "granted",
        "purpose": row.purpose,
        "revoked_at": str(row.revoked_at) if row.revoked_at else None,
    }


def dpdp_readiness(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    granted = db.execute(select(CustomerConsent).where(CustomerConsent.tenant_id == tenant_id, CustomerConsent.status == "granted")).scalars().all()
    revoked = db.execute(select(CustomerConsent).where(CustomerConsent.tenant_id == tenant_id, CustomerConsent.status == "revoked")).scalars().all()
    return {
        "tenant_id": tenant_id,
        "consent_capture": True,
        "data_export": True,
        "data_delete": True,
        "dsar_workflow": True,
        "pii_redaction_to_llm": True,
        "marketing_opt_out": True,
        "counts": {"granted_consents": len(granted), "revoked_consents": len(revoked)},
    }


def create_data_principal_request(
    db: Session,
    tenant_id: str,
    phone: str,
    request_type: str,
    requester_email: str | None = None,
    purpose: str | None = None,
) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    request_type = str(request_type or "export").strip().lower()
    if request_type not in {"export", "delete", "consent_status", "opt_out"}:
        raise ValueError("Unsupported data principal request type")
    row = DataPrincipalRequestLog(
        tenant_id=tenant_id,
        phone=phone,
        request_type=request_type,
        requester_email=requester_email,
        purpose=purpose or _default_purpose(request_type),
        status="received",
        due_at=datetime.utcnow() + timedelta(days=30),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return serialize_data_principal_request(row)


def list_data_principal_requests(db: Session, tenant_id: str, status: str | None = None) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id)
    statement = select(DataPrincipalRequestLog).where(DataPrincipalRequestLog.tenant_id == tenant_id)
    if status:
        statement = statement.where(DataPrincipalRequestLog.status == status)
    rows = db.execute(statement.order_by(DataPrincipalRequestLog.created_at.desc()).limit(200)).scalars().all()
    return [serialize_data_principal_request(row) for row in rows]


def resolve_data_principal_request(db: Session, tenant_id: str, request_id: int, status: str = "completed", result_summary: str | None = None) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(
        select(DataPrincipalRequestLog).where(
            DataPrincipalRequestLog.tenant_id == tenant_id,
            DataPrincipalRequestLog.id == int(request_id),
        )
    ).scalars().first()
    if not row:
        raise ValueError("Data principal request not found")
    row.status = str(status or "completed")
    row.completed_at = datetime.utcnow() if row.status in {"completed", "rejected"} else None
    row.result_summary = result_summary or row.result_summary
    db.commit()
    db.refresh(row)
    return serialize_data_principal_request(row)


def export_customer_data(db: Session, tenant_id: str, phone: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    return {
        "tenant_id": tenant_id,
        "phone": phone,
        "messages": [_row_dict(row) for row in db.execute(select(Message).where(Message.tenant_id == tenant_id, Message.phone == phone)).scalars().all()],
        "events": [_row_dict(row) for row in db.execute(select(WebhookEvent).where(WebhookEvent.tenant_id == tenant_id, WebhookEvent.phone == phone)).scalars().all()],
        "orders": [_row_dict(row) for row in db.execute(select(EcommerceOrder).where(EcommerceOrder.tenant_id == tenant_id, EcommerceOrder.phone == phone)).scalars().all()],
        "carts": [_row_dict(row) for row in db.execute(select(EcommerceCart).where(EcommerceCart.tenant_id == tenant_id, EcommerceCart.phone == phone)).scalars().all()],
        "returns": [_row_dict(row) for row in db.execute(select(EcommerceReturnRequest).where(EcommerceReturnRequest.tenant_id == tenant_id, EcommerceReturnRequest.phone == phone)).scalars().all()],
        "leads": [_row_dict(row) for row in db.execute(select(Lead).where(Lead.tenant_id == tenant_id, Lead.phone == phone)).scalars().all()],
        "consents": [_row_dict(row) for row in db.execute(select(CustomerConsent).where(CustomerConsent.tenant_id == tenant_id, CustomerConsent.phone == phone)).scalars().all()],
        "csat": [_row_dict(row) for row in db.execute(select(CSATResponse).where(CSATResponse.tenant_id == tenant_id, CSATResponse.phone == phone)).scalars().all()],
    }


def delete_customer_data(db: Session, tenant_id: str, phone: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    targets = [
        (Message, Message.tenant_id == tenant_id, Message.phone == phone),
        (WebhookEvent, WebhookEvent.tenant_id == tenant_id, WebhookEvent.phone == phone),
        (WhatsappInteractionEvent, WhatsappInteractionEvent.tenant_id == tenant_id, WhatsappInteractionEvent.phone == phone),
        (EcommerceCart, EcommerceCart.tenant_id == tenant_id, EcommerceCart.phone == phone),
        (EcommerceReturnRequest, EcommerceReturnRequest.tenant_id == tenant_id, EcommerceReturnRequest.phone == phone),
        (Lead, Lead.tenant_id == tenant_id, Lead.phone == phone),
        (CustomerConsent, CustomerConsent.tenant_id == tenant_id, CustomerConsent.phone == phone),
        (CSATResponse, CSATResponse.tenant_id == tenant_id, CSATResponse.phone == phone),
    ]
    deleted = {}
    for model, *conditions in targets:
        result = db.execute(delete(model).where(*conditions))
        deleted[model.__tablename__] = result.rowcount or 0
    db.commit()
    return {"status": "deleted", "tenant_id": tenant_id, "phone": phone, "deleted": deleted}


def serialize_data_principal_request(row: DataPrincipalRequestLog) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "phone": row.phone,
        "request_type": row.request_type,
        "status": row.status,
        "purpose": row.purpose,
        "requester_email": row.requester_email,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "result_summary": row.result_summary,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _default_purpose(request_type: str) -> str:
    return {
        "export": "Data principal access request",
        "delete": "Data principal erasure request",
        "consent_status": "Consent status request",
        "opt_out": "Marketing opt-out request",
    }.get(request_type, "Data principal rights request")


def _row_dict(row) -> dict:
    return {column.name: str(getattr(row, column.name)) if getattr(row, column.name) is not None else None for column in row.__table__.columns}
