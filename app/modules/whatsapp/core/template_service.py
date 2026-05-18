import json
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import Message, WhatsappTemplate
from app.modules.whatsapp.core.live_chat_socket import live_chat_manager
from app.modules.whatsapp.core.whatsapp_setup_service import get_whatsapp_credential


REQUEST_TIMEOUT = 30
TEMPLATE_STATUSES = {"PENDING", "APPROVED", "REJECTED", "IN_REVIEW"}


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
    graph_body = None
    status = "PENDING"
    wa_template_id = None
    if credential and credential.token and credential.waba_id:
        graph_body = _create_graph_template(credential.waba_id, credential.token, payload)
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


def get_template_by_id(db: Session, template_id: int) -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row:
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
    if credential and credential.token and row.wa_template_id:
        _post_graph(f"{settings.whatsapp_base_url}/{row.wa_template_id}", credential.token, payload)

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
    if credential and credential.token and credential.waba_id:
        _delete_graph_template(credential.waba_id, credential.token, row.name)
    db.delete(row)
    db.commit()
    return {"success": True, "statusCode": 1, "message": "Template deleted successfully"}


def get_template_status(db: Session, template_id: int, tenant_id: str = "default") -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row or row.tenant_id != tenant_id:
        return _error("Template not found", 404)
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    response = {"status": row.status, "id": row.wa_template_id}
    if credential and credential.token and row.wa_template_id:
        response = _get_graph(f"{settings.whatsapp_base_url}/{row.wa_template_id}", credential.token, {"fields": "status"})
        row.status = str(response.get("status") or row.status).upper()
        db.commit()
    return {"success": True, "statusCode": 1, "message": "Template status get successfully", "data": response}


def sync_templates(db: Session, tenant_id: str = "default") -> dict:
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    if not credential or not credential.token or not credential.waba_id:
        return _error("WhatsApp credential not found for the user", 400)
    body = _get_graph(f"{settings.whatsapp_base_url}/{credential.waba_id}/message_templates", credential.token)
    synced_count = 0
    for item in reversed(body.get("data") or []):
        external_id = str(item.get("id") or "")
        exists = db.execute(
            select(WhatsappTemplate).where(
                WhatsappTemplate.tenant_id == tenant_id,
                WhatsappTemplate.wa_template_id == external_id,
            )
        ).scalars().first()
        if exists:
            exists.status = str(item.get("status") or exists.status).upper()
            exists.components = json.dumps(item.get("components") or _loads(exists.components, []))
            continue
        db.add(
            WhatsappTemplate(
                tenant_id=tenant_id,
                phone_number=credential.phone_number,
                waba_id=credential.waba_id,
                wa_template_id=external_id or None,
                name=str(item.get("name") or ""),
                language=str(item.get("language") or "en"),
                category=str(item.get("category") or "UTILITY").upper(),
                parameter_format=str(item.get("parameter_format") or "POSITIONAL").upper(),
                components=json.dumps(item.get("components") or []),
                status=str(item.get("status") or "APPROVED").upper(),
                message_send_ttl_seconds=300,
            )
        )
        synced_count += 1
    db.commit()
    return {"success": True, "statusCode": 1, "message": "New templates synced successfully", "data": {"synced_count": synced_count}}


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


def auth_template_preview(
    db: Session,
    *,
    languages: str | None,
    add_security_recommendation: bool,
    code_expiration_minutes: int | None,
    tenant_id: str = "default",
) -> dict:
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    if credential and credential.token and credential.waba_id:
        params = {
            "category": "AUTHENTICATION",
            "button_types": "OTP",
            "add_security_recommendation": str(add_security_recommendation).lower(),
        }
        if languages:
            params["languages"] = languages
        if code_expiration_minutes:
            params["code_expiration_minutes"] = str(code_expiration_minutes)
        data = _get_graph(
            f"{settings.whatsapp_base_url}/{credential.waba_id}/message_template_previews",
            credential.token,
            params,
        ).get("data", [])
    else:
        data = [
            {
                "language": lang.strip(),
                "body": "{{1}} is your verification code. For your security, do not share this code."
                if add_security_recommendation
                else "{{1}} is your verification code.",
                "code_expiration_minutes": code_expiration_minutes,
                "buttons": [{"text": "Copy code", "autofill_text": "Auto-fill Code"}],
            }
            for lang in (languages or "en").split(",")
            if lang.strip()
        ]
    return {"success": True, "statusCode": 1, "message": "Authentication template preview fetched successfully", "data": data}


def send_template_message(
    db: Session,
    *,
    to_no: str,
    template_id: int,
    variables: dict[str, Any] | None = None,
    tenant_id: str = "default",
) -> dict:
    row = db.get(WhatsappTemplate, int(template_id))
    if not row:
        return _error("Template not found", 404)
    if row.status != "APPROVED":
        return _error("Template not approved", 400)
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    if not credential or not credential.token or not credential.phone_number_id:
        return _error("WhatsApp credentials are not configured", 400)
    components = _build_template_components(row, variables or {})
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_no,
        "type": "template",
        "template": {
            "name": row.name,
            "language": {"code": row.language},
            "components": components,
        },
    }
    response = _post_graph(f"{settings.whatsapp_base_url}/{credential.phone_number_id}/messages", credential.token, payload)
    msg_id = ((response.get("messages") or [{}])[0]).get("id")
    message = Message(
        phone=to_no,
        message=f"Template: {row.name}",
        direction="outgoing",
        status="sent",
        message_type="template",
        whatsapp_message_id=msg_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    data = {
        "id": message.whatsapp_message_id or message.id,
        "msg_id": message.whatsapp_message_id or message.id,
        "to_no": to_no,
        "from_no": "",
        "message_body": message.message,
        "message_type": "template",
        "direction": "out",
        "status": "sent",
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "template_body": {
            "name": row.name,
            "language": row.language,
            "category": row.category,
            "parameter_format": row.parameter_format,
            "components": _loads(row.components, []),
        },
        "template_varibales": variables or {},
    }
    return {"success": True, "statusCode": 1, "message": "Template Message sent successfully", "data": data}


def _build_template_components(template: WhatsappTemplate, variables: dict[str, Any]) -> list[dict]:
    components = []
    for component in _loads(template.components, []):
        if str(component.get("type", "")).upper() != "BODY":
            continue
        text = str(component.get("text") or "")
        if template.parameter_format == "NAMED":
            names = _extract_named_params(text)
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "parameter_name": name, "text": str(variables.get(name, ""))}
                        for name in names
                    ],
                }
            )
        else:
            positions = _extract_positional_params(text)
            values = list(variables.values())
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(values[index] if index < len(values) else "")}
                        for index, _ in enumerate(positions)
                    ],
                }
            )
    return components


def _create_graph_template(waba_id: str, token: str, payload: dict[str, Any]) -> dict:
    endpoint = "upsert_message_templates" if str(payload.get("category")).upper() == "AUTHENTICATION" else "message_templates"
    return _post_graph(f"{settings.whatsapp_base_url}/{waba_id}/{endpoint}", token, payload)


def _delete_graph_template(waba_id: str, token: str, name: str) -> dict:
    response = requests.delete(
        f"{settings.whatsapp_base_url}/{waba_id}/message_templates",
        params={"name": name},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse_response(response)


def _get_graph(url: str, token: str, params: dict[str, Any] | None = None) -> dict:
    response = requests.get(url, params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
    return _parse_response(response)


def _post_graph(url: str, token: str, payload: dict[str, Any]) -> dict:
    response = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse_response(response)


def _parse_response(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text}
    if not 200 <= response.status_code < 300:
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        raise RuntimeError(message or f"Meta API request failed with {response.status_code}")
    return body if isinstance(body, dict) else {"data": body}


def _extract_named_params(text: str) -> list[str]:
    import re

    return sorted(set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", text)))


def _extract_positional_params(text: str) -> list[int]:
    import re

    return sorted({int(value) for value in re.findall(r"{{\s*(\d+)\s*}}", text)})


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
