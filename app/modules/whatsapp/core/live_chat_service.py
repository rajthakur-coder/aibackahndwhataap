from datetime import datetime

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contact import Contact, ContactTag, Tag
from app.models.whatsapp import Message
from app.modules.whatsapp.core.whatsapp_setup_service import get_whatsapp_credential


def serialize_message(row: Message) -> dict:
    direction = "out" if row.direction == "outgoing" else "in"
    return {
        "id": row.whatsapp_message_id or row.id,
        "msg_id": row.whatsapp_message_id or row.id,
        "from_no": "" if direction == "out" else row.phone,
        "to_no": row.phone if direction == "out" else "",
        "message_body": row.message,
        "message_type": row.message_type or "text",
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
    _ensure_message_contacts(db)
    rows = _build_contact_rows(db)
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


def _ensure_message_contacts(db: Session) -> None:
    phones = db.execute(select(Message.phone).distinct()).scalars().all()
    for phone in phones:
        if not phone:
            continue
        existing = db.execute(select(Contact).where(Contact.phone == str(phone))).scalars().first()
        if not existing:
            db.add(
                Contact(
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


def _build_contact_rows(db: Session) -> list[dict]:
    contacts = db.execute(select(Contact)).scalars().all()
    rows = []
    for contact in contacts:
        latest = db.execute(
            select(Message)
            .where(Message.phone == contact.phone)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).scalars().first()
        unread_count = db.execute(
            select(func.count(Message.id)).where(
                Message.phone == contact.phone,
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
                "last_incoming_msg_time": latest.created_at.isoformat()
                if latest and latest.direction == "incoming" and latest.created_at
                else None,
                "unread_count": unread_count,
                "status": contact.status or "Active",
                "isWindowOpen": True,
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
    phone = _normalize_phone(customer_phone_number)
    if not phone or not custom_name.strip():
        return {"success": False, "statusCode": 0, "message": "customer_phone_number and custom_name are required"}

    contact = db.execute(select(Contact).where(Contact.phone == phone)).scalars().first()
    if not contact:
        contact = Contact(phone=phone, profile_name=phone, created_at=datetime.utcnow())
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
    allowed = {"Active", "Inactive", "Banned", "Blocked", "Archived"}
    if status not in allowed:
        return {"success": False, "statusCode": 0, "message": f"Invalid status. Allowed values: {', '.join(sorted(allowed))}"}
    phone = _normalize_phone(customer_phone_number)
    contact = db.execute(select(Contact).where(Contact.phone == phone)).scalars().first()
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
    phone = _normalize_phone(customer_phone_number)
    contact = db.execute(select(Contact).where(Contact.phone == phone)).scalars().first()
    if not contact:
        return {"success": False, "statusCode": 0, "message": "No active contact found"}
    db.delete(contact)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Contact and all related data deleted successfully"}


def list_tags(db: Session, *, search: str = "", offset: int = 0, limit: int = 20, status: str = "true") -> dict:
    statement = select(Tag)
    if status != "false":
        statement = statement.where(Tag.status == "Active")
    if search.strip():
        statement = statement.where(Tag.name.ilike(f"%{search.strip()}%"))
    rows = db.execute(statement.order_by(Tag.created_at.desc())).scalars().all()
    total = len(rows)
    start = max(0, int(offset)) * max(1, int(limit))
    page = rows[start : start + max(1, int(limit))]
    return {
        "success": True,
        "statusCode": 1,
        "message": "Tags fetched successfully",
        "data": [_serialize_tag(row) for row in page],
        "recordsTotal": total,
        "recordsFiltered": total,
    }


def create_tag(db: Session, *, name: str, color: str | None = None, description: str | None = None) -> dict:
    clean_name = name.strip()
    if not clean_name:
        return {"success": False, "statusCode": 0, "message": "Tag name is required"}
    existing = db.execute(select(Tag).where(func.lower(Tag.name) == clean_name.lower())).scalars().first()
    if existing:
        return {"success": True, "statusCode": 1, "message": "Tag already exists", "data": _serialize_tag(existing)}
    tag = Tag(name=clean_name, color=color, description=description, status="Active")
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"success": True, "statusCode": 1, "message": "Tag created successfully", "data": _serialize_tag(tag)}


def assign_tags_to_contact(db: Session, *, contact_id: int, tag_ids: list[int]) -> dict:
    contact = db.get(Contact, contact_id)
    if not contact:
        return {"success": False, "statusCode": 0, "message": "Contact not found"}
    valid_tags = db.execute(select(Tag).where(Tag.id.in_(tag_ids), Tag.status == "Active")).scalars().all()
    valid_ids = {tag.id for tag in valid_tags}
    if len(valid_ids) != len(set(tag_ids)):
        return {"success": False, "statusCode": 0, "message": "One or more invalid tags"}
    existing_ids = {contact_tag.tag_id for contact_tag in contact.contact_tags}
    for tag_id in valid_ids - existing_ids:
        db.add(ContactTag(contact_id=contact.id, tag_id=tag_id))
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Tags assigned successfully"}


def remove_tag_from_contact(db: Session, *, contact_id: int, tag_id: int) -> dict:
    contact_tag = db.execute(
        select(ContactTag).where(ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id)
    ).scalars().first()
    if not contact_tag:
        return {"success": False, "statusCode": 0, "message": "Tag assignment not found"}
    db.delete(contact_tag)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Tag removed successfully"}


def _serialize_tag(tag: Tag) -> dict:
    return {
        "id": tag.id,
        "name": tag.name,
        "color": tag.color,
        "description": tag.description,
        "status": tag.status == "Active",
        "created_at": tag.created_at.isoformat() if tag.created_at else None,
        "updated_at": tag.updated_at.isoformat() if tag.updated_at else None,
    }


def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone or "").replace("+", "") if ch.isdigit())


def get_chat_messages(db: Session, contact: str) -> dict:
    rows = db.execute(
        select(Message)
        .where(Message.phone == contact)
        .order_by(Message.created_at.asc(), Message.id.asc())
    ).scalars().all()

    for row in rows:
        if row.direction == "incoming" and row.status != "read":
            row.status = "read"
    db.commit()

    return {
        "success": True,
        "statusCode": 1,
        "message": "messages fetched",
        "data": [serialize_message(row) for row in rows],
    }


def send_live_chat_text(
    db: Session,
    *,
    to_no: str,
    message_body: str,
) -> dict:
    credential = get_whatsapp_credential(db)
    if not credential or not credential.token or not credential.phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")

    payload = {
        "messaging_product": "whatsapp",
        "to": to_no,
        "type": "text",
        "text": {"body": message_body[:4096]},
    }
    response = requests.post(
        f"{settings.whatsapp_base_url}/{credential.phone_number_id}/messages",
        headers={
            "Authorization": f"Bearer {credential.token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    whatsapp_message_id = (body.get("messages") or [{}])[0].get("id")

    row = Message(
        phone=to_no,
        message=message_body,
        direction="outgoing",
        status="sent",
        message_type="text",
        whatsapp_message_id=whatsapp_message_id,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    return {
        "success": True,
        "statusCode": 1,
        "message": "Message sent successfully",
        "data": serialize_message(row),
    }


def mark_chat_read(db: Session, message_id: str | None = None, contact: str | None = None) -> dict:
    statement = select(Message).where(Message.direction == "incoming")
    if contact:
        statement = statement.where(Message.phone == contact)
    rows = db.execute(statement).scalars().all()
    for row in rows:
        if message_id and str(row.whatsapp_message_id or row.id) != str(message_id):
            continue
        row.status = "read"
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Message status updated"}
