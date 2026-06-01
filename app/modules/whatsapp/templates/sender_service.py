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


from app.modules.whatsapp.templates.repository_service import *

def auth_template_preview(
    db: Session,
    *,
    languages: str | None,
    add_security_recommendation: bool,
    code_expiration_minutes: int | None,
    tenant_id: str = "default",
) -> dict:
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if credential and token and credential.waba_id:
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
            f"{settings.WHATSAPP_BASE_URL}/{credential.waba_id}/message_template_previews",
            token,
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
    token = whatsapp_access_token(credential)
    if not credential or not token or not credential.phone_number_id:
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
    response = _post_graph(f"{settings.WHATSAPP_BASE_URL}/{credential.phone_number_id}/messages", token, payload)
    msg_id = ((response.get("messages") or [{}])[0]).get("id")
    message = Message(
        tenant_id=tenant_id,
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

def _extract_named_params(text: str) -> list[str]:
    import re

    return sorted(set(re.findall(r"{{\s*([a-zA-Z0-9_]+)\s*}}", text)))

def _extract_positional_params(text: str) -> list[int]:
    import re

    return sorted({int(value) for value in re.findall(r"{{\s*(\d+)\s*}}", text)})

__all__ = [
    "auth_template_preview",
    "send_template_message",
    "_build_template_components",
    "_extract_named_params",
    "_extract_positional_params",
]
