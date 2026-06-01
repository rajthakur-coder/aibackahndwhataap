import json
from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contact import Contact, ContactTag, Tag
from app.models.whatsapp import Message
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


def serialize_message(row: Message) -> dict:
    direction = "out" if row.direction == "outgoing" else "in"
    payload = None
    if row.payload:
        try:
            payload = json.loads(row.payload)
        except json.JSONDecodeError:
            payload = row.payload
    return {
        "id": row.whatsapp_message_id or row.id,
        "msg_id": row.whatsapp_message_id or row.id,
        "from_no": "" if direction == "out" else row.phone,
        "to_no": row.phone if direction == "out" else "",
        "message_body": row.message,
        "message_type": row.message_type or "text",
        "payload": payload,
        "direction": direction,
        "status": row.status or ("sent" if direction == "out" else "received"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }

def list_chat_contacts(
    db: Session,
    *,
    offset: int = 0,
    limit: int = 15,
    search_value: str = "",
    status: str | None = None,
    tags: str | None = None,
    tag_ids: str | None = None,
) -> dict:
    tenant_id = _live_chat_tenant_id()
    _ensure_message_contacts(db, tenant_id=tenant_id)
    rows = _build_contact_rows(db, tenant_id=tenant_id)
    records_total = len(rows)
    total_active = sum(1 for row in rows if row["status"] == "Active")
    total_blocked = sum(1 for row in rows if row["status"] == "Blocked")
    total_inactive = sum(1 for row in rows if row["status"] == "Inactive")

    tag_names = {item.strip() for item in (tags or "").split(",") if item.strip()}
    tag_id_values = {int(item) for item in (tag_ids or "").split(",") if item.strip().isdigit()}

    filtered = []
    normalized_search = search_value.lower().strip()
    for row in rows:
        if status and row["status"] != status:
            continue
        if normalized_search:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in ("custom_name", "profile_name", "customer_phone_number", "last_message")
            ).lower()
            if normalized_search not in haystack:
                continue
        if tag_names or tag_id_values:
            row_tags = row.get("contact_tags", [])
            if tag_names and not any(tag["name"] in tag_names for tag in row_tags):
                continue
            if tag_id_values and not any(int(tag["id"]) in tag_id_values for tag in row_tags):
                continue
        filtered.append(row)

    filtered.sort(key=lambda item: item.get("last_message_time") or item.get("created_at") or "", reverse=True)
    page_limit = max(1, int(limit))
    start = max(0, int(offset)) * page_limit
    page = filtered[start : start + page_limit]
    for index, row in enumerate(page, start=start + 1):
        row["sr_no"] = index

    return {
        "success": True,
        "statusCode": 1,
        "message": "contact fetched successfully",
        "data": page,
        "recordsTotal": records_total,
        "recordsFiltered": len(filtered),
        "total_active": total_active,
        "total_blocked": total_blocked,
        "total_inactive": total_inactive,
    }

def _ensure_message_contacts(db: Session, *, tenant_id: str | None = None) -> None:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    phones = db.execute(
        select(Message.phone).where(Message.tenant_id == tenant_id).distinct()
    ).scalars().all()
    for phone in phones:
        if not phone:
            continue
        existing = db.execute(
            select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == str(phone))
        ).scalars().first()
        if not existing:
            db.add(
                Contact(
                    tenant_id=tenant_id,
                    phone=str(phone),
                    profile_name=str(phone),
                    name=None,
                    custom_name=None,
                    status="Active",
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
            )
    db.commit()

def _build_contact_rows(db: Session, *, tenant_id: str | None = None) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    contacts = db.execute(select(Contact).where(Contact.tenant_id == tenant_id)).scalars().all()
    rows = []
    for contact in contacts:
        latest = db.execute(
            select(Message)
            .where(Message.tenant_id == tenant_id, Message.phone == contact.phone)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).scalars().first()
        latest_incoming = db.execute(
            select(Message)
            .where(
                Message.tenant_id == tenant_id,
                Message.phone == contact.phone,
                Message.direction == "incoming",
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).scalars().first()
        unread_count = db.execute(
            select(func.count(Message.id)).where(
                Message.phone == contact.phone,
                Message.tenant_id == tenant_id,
                Message.direction == "incoming",
                Message.status != "read",
            )
        ).scalar_one()
        contact_tags = [
            {
                "id": contact_tag.tag.id,
                "name": contact_tag.tag.name,
                "color": contact_tag.tag.color,
                "created_at": contact_tag.created_at.isoformat() if contact_tag.created_at else None,
            }
            for contact_tag in contact.contact_tags
            if contact_tag.tag and contact_tag.tag.status == "Active"
        ]
        rows.append(
            {
                "id": str(contact.id),
                "phone_number": "",
                "customer_phone_number": contact.phone,
                "profile_name": contact.profile_name or contact.phone,
                "custom_name": contact.custom_name or contact.name,
                "remark": contact.remark,
                "last_message": latest.message if latest else "",
                "last_message_type": (latest.message_type or "text") if latest else "text",
                "last_message_time": latest.created_at.isoformat() if latest and latest.created_at else None,
                "last_incoming_msg_time": latest_incoming.created_at.isoformat()
                if latest_incoming and latest_incoming.created_at
                else None,
                "unread_count": unread_count,
                "status": contact.status or "Active",
                "isWindowOpen": _is_24_hour_window_open(
                    latest_incoming.created_at if latest_incoming else None
                ),
                "contact_tags": contact_tags,
                "created_at": contact.created_at.isoformat() if contact.created_at else None,
                "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
            }
        )
    return rows

def upsert_manual_contact(
    db: Session,
    *,
    customer_phone_number: str,
    custom_name: str,
    remark: str | None = None,
) -> dict:
    tenant_id = _live_chat_tenant_id()
    phone = _normalize_phone(customer_phone_number)
    if not phone or not custom_name.strip():
        return {"success": False, "statusCode": 0, "message": "customer_phone_number and custom_name are required"}

    contact = db.execute(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == phone)
    ).scalars().first()
    if not contact:
        contact = Contact(
            tenant_id=tenant_id,
            phone=phone,
            profile_name=phone,
            created_at=datetime.utcnow(),
        )
        db.add(contact)
    contact.custom_name = custom_name.strip()
    contact.name = custom_name.strip()
    if remark is not None:
        contact.remark = remark
    contact.status = contact.status or "Active"
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    return {"success": True, "statusCode": 1, "message": "Contact saved successfully", "data": {"id": contact.id}}

def update_contact_status(db: Session, *, customer_phone_number: str, status: str) -> dict:
    tenant_id = _live_chat_tenant_id()
    allowed = {"Active", "Inactive", "Banned", "Blocked", "Archived"}
    if status not in allowed:
        return {"success": False, "statusCode": 0, "message": f"Invalid status. Allowed values: {', '.join(sorted(allowed))}"}
    phone = _normalize_phone(customer_phone_number)
    contact = db.execute(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == phone)
    ).scalars().first()
    if not contact:
        return {"success": False, "statusCode": 0, "message": "No active contact found with this number"}
    contact.status = status
    contact.updated_at = datetime.utcnow()
    db.commit()
    return {
        "success": True,
        "statusCode": 1,
        "message": "Status updated successfully",
        "data": {"customer_phone_number": phone, "status": status},
    }

def delete_contact(db: Session, *, customer_phone_number: str) -> dict:
    tenant_id = _live_chat_tenant_id()
    phone = _normalize_phone(customer_phone_number)
    contact = db.execute(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == phone)
    ).scalars().first()
    if not contact:
        return {"success": False, "statusCode": 0, "message": "No active contact found"}
    db.delete(contact)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Contact and all related data deleted successfully"}

def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone or "").replace("+", "") if ch.isdigit())

def _live_chat_tenant_id() -> str:
    return normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)

def _is_24_hour_window_open(last_incoming_time: datetime | None) -> bool:
    if not last_incoming_time:
        return False
    return datetime.utcnow() - last_incoming_time <= timedelta(hours=24)

__all__ = [
    "serialize_message",
    "list_chat_contacts",
    "_ensure_message_contacts",
    "_build_contact_rows",
    "upsert_manual_contact",
    "update_contact_status",
    "delete_contact",
    "_normalize_phone",
    "_live_chat_tenant_id",
    "_is_24_hour_window_open",
]
