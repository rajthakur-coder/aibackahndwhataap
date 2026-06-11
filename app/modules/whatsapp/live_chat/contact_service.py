import json
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

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
    sync_missing_contacts: bool = False,
) -> dict:
    tenant_id = _live_chat_tenant_id()
    if sync_missing_contacts:
        _ensure_message_contacts(db, tenant_id=tenant_id)
    page_limit = max(1, int(limit))
    page_offset = max(0, int(offset))
    start = page_offset * page_limit
    normalized_search = search_value.lower().strip()
    tag_names = [item.strip() for item in (tags or "").split(",") if item.strip()]
    tag_id_values = [int(item) for item in (tag_ids or "").split(",") if item.strip().isdigit()]

    base_conditions = [Contact.tenant_id == tenant_id]
    filtered_conditions = [Contact.tenant_id == tenant_id]
    if status:
        filtered_conditions.append(Contact.status == status)
    if normalized_search:
        like = f"%{normalized_search}%"
        filtered_conditions.append(
            or_(
                func.lower(func.coalesce(Contact.custom_name, "")).like(like),
                func.lower(func.coalesce(Contact.name, "")).like(like),
                func.lower(func.coalesce(Contact.profile_name, "")).like(like),
                func.lower(func.coalesce(Contact.phone, "")).like(like),
                func.lower(func.coalesce(Contact.last_message, "")).like(like),
            )
        )
    if tag_names or tag_id_values:
        tag_conditions = []
        if tag_names:
            tag_conditions.append(Tag.name.in_(tag_names))
        if tag_id_values:
            tag_conditions.append(Tag.id.in_(tag_id_values))
        tag_filter = Contact.contact_tags.any(
            ContactTag.tag.has(or_(*tag_conditions))
        )
        filtered_conditions.append(tag_filter)

    status_counts = dict(
        db.execute(
            select(Contact.status, func.count(Contact.id))
            .where(*base_conditions)
            .group_by(Contact.status)
        ).all()
    )
    records_total = sum(status_counts.values())
    records_filtered = db.execute(
        select(func.count(Contact.id)).where(*filtered_conditions)
    ).scalar_one()

    contacts = db.execute(
        select(Contact)
        .options(selectinload(Contact.contact_tags).selectinload(ContactTag.tag))
        .where(*filtered_conditions)
        .order_by(Contact.last_message_time.desc().nullslast(), Contact.created_at.desc())
        .offset(start)
        .limit(page_limit)
    ).scalars().all()

    page = [
        _serialize_contact_summary(contact, sr_no=start + index)
        for index, contact in enumerate(contacts, start=1)
    ]

    return {
        "success": True,
        "statusCode": 1,
        "message": "contact fetched successfully",
        "data": page,
        "recordsTotal": records_total,
        "recordsFiltered": records_filtered,
        "total_active": status_counts.get("Active", 0),
        "total_blocked": status_counts.get("Blocked", 0),
        "total_inactive": status_counts.get("Inactive", 0),
    }

def _serialize_contact_summary(contact: Contact, *, sr_no: int) -> dict:
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
    return {
        "sr_no": sr_no,
        "id": str(contact.id),
        "phone_number": "",
        "customer_phone_number": contact.phone,
        "profile_name": contact.profile_name or contact.phone,
        "custom_name": contact.custom_name or contact.name,
        "remark": contact.remark,
        "last_message": contact.last_message or "",
        "last_message_type": contact.last_message_type or "text",
        "last_message_time": contact.last_message_time.isoformat()
        if contact.last_message_time
        else None,
        "last_incoming_msg_time": contact.last_incoming_msg_time.isoformat()
        if contact.last_incoming_msg_time
        else None,
        "unread_count": contact.unread_count or 0,
        "status": contact.status or "Active",
        "isWindowOpen": _is_24_hour_window_open(contact.last_incoming_msg_time),
        "contact_tags": contact_tags,
        "created_at": contact.created_at.isoformat() if contact.created_at else None,
        "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
    }

def _ensure_message_contacts(db: Session, *, tenant_id: str | None = None) -> None:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    phones = {
        str(phone)
        for phone in db.execute(
            select(Message.phone).where(Message.tenant_id == tenant_id).distinct()
        ).scalars().all()
        if phone
    }
    if not phones:
        return

    existing_phones = set(
        db.execute(
            select(Contact.phone).where(
                Contact.tenant_id == tenant_id,
                Contact.phone.in_(phones),
            )
        ).scalars().all()
    )
    missing_phones = phones - existing_phones
    if not missing_phones:
        return

    now = datetime.utcnow()
    for phone in missing_phones:
        db.add(
            Contact(
                tenant_id=tenant_id,
                phone=phone,
                profile_name=phone,
                name=None,
                custom_name=None,
                status="Active",
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()

def _build_contact_rows(db: Session, *, tenant_id: str | None = None) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    contacts = db.execute(
        select(Contact).where(Contact.tenant_id == tenant_id)
    ).scalars().all()
    if not contacts:
        return []

    contact_ids = [contact.id for contact in contacts]
    phones = [str(contact.phone) for contact in contacts if contact.phone]
    latest_messages = _latest_messages_by_phone(db, tenant_id=tenant_id, phones=phones)
    latest_incoming_messages = _latest_messages_by_phone(
        db,
        tenant_id=tenant_id,
        phones=phones,
        direction="incoming",
    )
    unread_counts = _unread_counts_by_phone(db, tenant_id=tenant_id, phones=phones)
    tags_by_contact = _tags_by_contact_id(db, tenant_id=tenant_id, contact_ids=contact_ids)

    rows = []
    for contact in contacts:
        phone = str(contact.phone or "")
        latest = latest_messages.get(phone)
        latest_incoming = latest_incoming_messages.get(phone)
        contact_tags = tags_by_contact.get(contact.id, [])
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
                "unread_count": unread_counts.get(phone, 0),
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

def update_contact_from_message(
    db: Session,
    *,
    phone: str,
    message: str,
    direction: str,
    message_type: str = "text",
    created_at: datetime | None = None,
    tenant_id: str | None = None,
) -> Contact:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    normalized_phone = _normalize_phone(phone)
    now = created_at or datetime.utcnow()
    contact = db.execute(
        select(Contact).where(
            Contact.tenant_id == tenant_id,
            Contact.phone == normalized_phone,
        )
    ).scalars().first()

    if not contact:
        contact = Contact(
            tenant_id=tenant_id,
            phone=normalized_phone,
            profile_name=normalized_phone,
            status="Active",
            created_at=now,
        )
        db.add(contact)

    contact.last_message = message
    contact.last_message_type = message_type or "text"
    contact.last_message_time = now
    contact.updated_at = datetime.utcnow()

    if direction == "incoming":
        contact.last_incoming_msg_time = now
        contact.unread_count = (contact.unread_count or 0) + 1
    elif contact.unread_count is None:
        contact.unread_count = 0

    db.commit()
    db.refresh(contact)
    return contact

def clear_contact_unread(
    db: Session,
    *,
    phone: str,
    tenant_id: str | None = None,
) -> None:
    tenant_id = normalize_tenant_id(tenant_id or _live_chat_tenant_id())
    normalized_phone = _normalize_phone(phone)
    if not normalized_phone:
        return

    contact = db.execute(
        select(Contact).where(
            Contact.tenant_id == tenant_id,
            Contact.phone == normalized_phone,
        )
    ).scalars().first()
    if not contact:
        return

    contact.unread_count = 0
    contact.updated_at = datetime.utcnow()
    db.commit()

def _latest_messages_by_phone(
    db: Session,
    *,
    tenant_id: str,
    phones: list[str],
    direction: str | None = None,
) -> dict[str, Message]:
    if not phones:
        return {}

    conditions = [Message.tenant_id == tenant_id, Message.phone.in_(phones)]
    if direction:
        conditions.append(Message.direction == direction)

    ranked_messages = (
        select(
            Message.id.label("id"),
            func.row_number()
            .over(
                partition_by=Message.phone,
                order_by=[Message.created_at.desc(), Message.id.desc()],
            )
            .label("rank"),
        )
        .where(*conditions)
        .subquery()
    )

    rows = db.execute(
        select(Message)
        .join(ranked_messages, Message.id == ranked_messages.c.id)
        .where(ranked_messages.c.rank == 1)
    ).scalars().all()
    return {str(row.phone): row for row in rows if row.phone}

def _unread_counts_by_phone(db: Session, *, tenant_id: str, phones: list[str]) -> dict[str, int]:
    if not phones:
        return {}

    rows = db.execute(
        select(Message.phone, func.count(Message.id))
        .where(
            Message.tenant_id == tenant_id,
            Message.phone.in_(phones),
            Message.direction == "incoming",
            Message.status != "read",
        )
        .group_by(Message.phone)
    ).all()
    return {str(phone): int(count or 0) for phone, count in rows if phone}

def _tags_by_contact_id(
    db: Session,
    *,
    tenant_id: str,
    contact_ids: list[int],
) -> dict[int, list[dict]]:
    if not contact_ids:
        return {}

    rows = db.execute(
        select(
            ContactTag.contact_id,
            Tag.id,
            Tag.name,
            Tag.color,
            ContactTag.created_at,
        )
        .join(Tag, ContactTag.tag_id == Tag.id)
        .where(
            ContactTag.tenant_id == tenant_id,
            ContactTag.contact_id.in_(contact_ids),
            Tag.status == "Active",
        )
    ).all()

    tags_by_contact: dict[int, list[dict]] = {}
    for contact_id, tag_id, name, color, created_at in rows:
        tags_by_contact.setdefault(int(contact_id), []).append(
            {
                "id": tag_id,
                "name": name,
                "color": color,
                "created_at": created_at.isoformat() if created_at else None,
            }
        )
    return tags_by_contact

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
    now = (
        datetime.now(timezone.utc)
        if last_incoming_time.tzinfo
        else datetime.utcnow()
    )
    return now - last_incoming_time <= timedelta(hours=24)

__all__ = [
    "serialize_message",
    "list_chat_contacts",
    "_ensure_message_contacts",
    "_build_contact_rows",
    "upsert_manual_contact",
    "update_contact_status",
    "delete_contact",
    "update_contact_from_message",
    "clear_contact_unread",
    "_normalize_phone",
    "_live_chat_tenant_id",
    "_is_24_hour_window_open",
]
