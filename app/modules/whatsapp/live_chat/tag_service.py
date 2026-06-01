from datetime import datetime, timedelta

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.contact import Contact, ContactTag, Tag
from app.models.whatsapp import Message
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential
from app.shared.tenant import DEFAULT_TENANT_ID, current_tenant_id, normalize_tenant_id


def list_tags(db: Session, *, search: str = "", offset: int = 0, limit: int = 20, status: str = "true") -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    statement = select(Tag).where(Tag.tenant_id == tenant_id)
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
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    clean_name = name.strip()
    if not clean_name:
        return {"success": False, "statusCode": 0, "message": "Tag name is required"}
    existing = db.execute(select(Tag).where(Tag.tenant_id == tenant_id, func.lower(Tag.name) == clean_name.lower())).scalars().first()
    if existing:
        return {"success": True, "statusCode": 1, "message": "Tag already exists", "data": _serialize_tag(existing)}
    tag = Tag(tenant_id=tenant_id, name=clean_name, color=color, description=description, status="Active")
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return {"success": True, "statusCode": 1, "message": "Tag created successfully", "data": _serialize_tag(tag)}

def assign_tags_to_contact(db: Session, *, contact_id: int, tag_ids: list[int]) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    contact = db.get(Contact, contact_id)
    if not contact or contact.tenant_id != tenant_id:
        return {"success": False, "statusCode": 0, "message": "Contact not found"}
    valid_tags = db.execute(select(Tag).where(Tag.tenant_id == tenant_id, Tag.id.in_(tag_ids), Tag.status == "Active")).scalars().all()
    valid_ids = {tag.id for tag in valid_tags}
    if len(valid_ids) != len(set(tag_ids)):
        return {"success": False, "statusCode": 0, "message": "One or more invalid tags"}
    existing_ids = {contact_tag.tag_id for contact_tag in contact.contact_tags}
    for tag_id in valid_ids - existing_ids:
        db.add(ContactTag(tenant_id=tenant_id, contact_id=contact.id, tag_id=tag_id))
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Tags assigned successfully"}

def remove_tag_from_contact(db: Session, *, contact_id: int, tag_id: int) -> dict:
    tenant_id = normalize_tenant_id(current_tenant_id() or DEFAULT_TENANT_ID)
    contact_tag = db.execute(
        select(ContactTag).where(ContactTag.tenant_id == tenant_id, ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id)
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

__all__ = [
    "list_tags",
    "create_tag",
    "assign_tags_to_contact",
    "remove_tag_from_contact",
    "_serialize_tag",
]
