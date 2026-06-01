import json
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import Message, WhatsappTemplate
from app.modules.whatsapp.live_chat.socket import live_chat_manager
from app.modules.whatsapp.setup.setup_service import get_whatsapp_credential, whatsapp_access_token


REQUEST_TIMEOUT = 30
TEMPLATE_STATUSES = {"PENDING", "APPROVED", "REJECTED", "IN_REVIEW"}
logger = logging.getLogger(__name__)


def serialize_template(row: WhatsappTemplate) -> dict:
    return {
        "id": row.id,
        "user_id": row.tenant_id,
        "phone_number": row.phone_number,
        "waba_id": row.waba_id,
        "template_id": row.wa_template_id,
        "wa_template_id": row.wa_template_id,
        "name": row.name,
        "language": row.language,
        "language_name": row.language_name,
        "category": row.category,
        "parameter_format": row.parameter_format,
        "components": _loads(row.components, []),
        "status": row.status,
        "quality_rating": row.quality_rating,
        "message_send_ttl_seconds": row.message_send_ttl_seconds,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }

def list_templates(
    db: Session,
    *,
    name: str = "",
    language: str = "",
    category: str = "",
    status: str = "",
    authentication: bool = False,
    offset: int = 0,
    limit: int = 20,
    tenant_id: str = "default",
) -> dict:
    statement = select(WhatsappTemplate).where(WhatsappTemplate.tenant_id == tenant_id)
    filtered = statement
    if name:
        filtered = filtered.where(WhatsappTemplate.name.ilike(f"%{name.strip()}%"))
    if language:
        filtered = filtered.where(WhatsappTemplate.language == language)
    if status:
        filtered = filtered.where(WhatsappTemplate.status == status)
    if category:
        filtered = filtered.where(WhatsappTemplate.category == category)
    elif not authentication:
        filtered = filtered.where(WhatsappTemplate.category.in_(["MARKETING", "UTILITY"]))

    total = db.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    total_filtered = db.execute(select(func.count()).select_from(filtered.subquery())).scalar_one()
    rows = db.execute(
        filtered.order_by(WhatsappTemplate.created_at.desc())
        .offset(max(0, int(offset)) * max(1, int(limit)))
        .limit(max(1, int(limit)))
    ).scalars().all()
    return _list_response("Templates fetched successfully", [serialize_template(row) for row in rows], total, total_filtered)

def register_template(db: Session, payload: dict[str, Any], tenant_id: str = "default") -> dict:
    name = str(payload.get("name") or "").strip()
    language = str(payload.get("language") or "").strip()
    category = str(payload.get("category") or "").upper().strip()
    components = payload.get("components")
    if not name or not language or category not in {"MARKETING", "UTILITY", "AUTHENTICATION"} or not isinstance(components, list):
        return _error("name, language, category and components are required", 400)

    existing = db.execute(
        select(WhatsappTemplate).where(
            WhatsappTemplate.tenant_id == tenant_id,
            WhatsappTemplate.name == name,
            WhatsappTemplate.language == language,
        )
    ).scalars().first()
    if existing:
        return _error("Template with the same name and language already exists", 409)

    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if not credential or not token or not credential.waba_id:
        logger.error(
            "WhatsApp template create skipped: missing credential tenant_id=%s has_credential=%s has_token=%s has_waba_id=%s",
            tenant_id,
            bool(credential),
            bool(token),
            bool(credential.waba_id) if credential else False,
        )
        return _error("WhatsApp credential not found for the user", 400)

    graph_body = None
    status = "PENDING"
    wa_template_id = None
    logger.info(
        "Creating WhatsApp template on Meta tenant_id=%s waba_id=%s name=%s language=%s category=%s",
        tenant_id,
        credential.waba_id,
        name,
        language,
        category,
    )
    graph_body = _create_graph_template(credential.waba_id, token, payload)
    logger.info(
        "Meta template create response name=%s language=%s response=%s",
        name,
        language,
        graph_body,
    )
    status = str(graph_body.get("status") or "PENDING").upper()
    wa_template_id = str(graph_body.get("id")) if graph_body.get("id") else None

    row = WhatsappTemplate(
        tenant_id=tenant_id,
        phone_number=credential.phone_number if credential else None,
        waba_id=credential.waba_id if credential else None,
        wa_template_id=wa_template_id,
        name=name,
        language=language,
        language_name=payload.get("language_name"),
        category=category,
        parameter_format=str(payload.get("parameter_format") or "POSITIONAL").upper(),
        components=json.dumps(components),
        status=status if status in TEMPLATE_STATUSES else "PENDING",
        message_send_ttl_seconds=int(payload.get("message_send_ttl_seconds") or 300),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"success": True, "statusCode": 1, "message": "Template registered successfully", "data": {"id": row.id, "status": row.status, "category": row.category, "graph": graph_body}}

def get_template_by_id(db: Session, template_id: int, tenant_id: str = "default") -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row or row.tenant_id != tenant_id:
        return _error("Template not found", 404)
    return {"success": True, "statusCode": 1, "message": "Template fetched successfully", "data": serialize_template(row)}

def update_template(db: Session, template_id: int, payload: dict[str, Any], tenant_id: str = "default") -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row or row.tenant_id != tenant_id:
        return _error("Template not found", 404)
    if payload.get("name") and payload.get("name") != row.name:
        return _error("Template name cannot be changed once created", 400)
    if payload.get("category") and str(payload.get("category")).upper() != row.category:
        return _error("Template category cannot be changed", 400)

    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if credential and token and row.wa_template_id:
        _post_graph(f"{settings.WHATSAPP_BASE_URL}/{row.wa_template_id}", token, payload)

    if "language" in payload:
        row.language = str(payload["language"])
    if "language_name" in payload:
        row.language_name = payload["language_name"]
    if "parameter_format" in payload:
        row.parameter_format = str(payload["parameter_format"]).upper()
    if "components" in payload and isinstance(payload["components"], list):
        row.components = json.dumps(payload["components"])
    if "message_send_ttl_seconds" in payload:
        row.message_send_ttl_seconds = int(payload["message_send_ttl_seconds"])
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Template updated successfully"}

def delete_template(db: Session, template_id: int, tenant_id: str = "default") -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row or row.tenant_id != tenant_id:
        return _error("Template not found", 404)
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if credential and token and credential.waba_id:
        _delete_graph_template(credential.waba_id, token, row.name)
    db.delete(row)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Template deleted successfully"}

def get_template_status(db: Session, template_id: int, tenant_id: str = "default") -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row or row.tenant_id != tenant_id:
        return _error("Template not found", 404)
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    response = {"status": row.status, "id": row.wa_template_id}
    if credential and token and row.wa_template_id:
        response = _get_graph(f"{settings.WHATSAPP_BASE_URL}/{row.wa_template_id}", token, {"fields": "status"})
        row.status = str(response.get("status") or row.status).upper()
        db.commit()
    return {"success": True, "statusCode": 1, "message": "Template status get successfully", "data": response}

def list_languages(db: Session, tenant_id: str = "default") -> dict:
    rows = db.execute(
        select(WhatsappTemplate.language, WhatsappTemplate.language_name)
        .where(WhatsappTemplate.tenant_id == tenant_id)
        .distinct()
    ).all()
    return {
        "success": True,
        "statusCode": 1,
        "message": "Language fetched successfully",
        "data": [
            {"serial_no": index + 1, "language": row.language, "language_name": row.language_name}
            for index, row in enumerate(rows)
        ],
    }

def _loads(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback

def _error(message: str, status_code: int = 400) -> dict:
    return {"success": False, "statusCode": 0, "message": message, "status_code": status_code}

def _list_response(message: str, data: list[dict], total: int, filtered: int) -> dict:
    return {
        "success": True,
        "statusCode": 1,
        "message": message,
        "recordsTotal": total,
        "recordsFiltered": filtered,
        "data": data,
    }

__all__ = [
    "serialize_template",
    "list_templates",
    "register_template",
    "get_template_by_id",
    "update_template",
    "delete_template",
    "get_template_status",
    "list_languages",
    "_loads",
    "_error",
    "_list_response",
]
