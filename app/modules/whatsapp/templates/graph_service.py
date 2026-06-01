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

def sync_templates(db: Session, tenant_id: str = "default") -> dict:
    credential = get_whatsapp_credential(db, tenant_id=tenant_id)
    token = whatsapp_access_token(credential)
    if not credential or not token or not credential.waba_id:
        return _error("WhatsApp credential not found for the user", 400)
    body = _get_graph(f"{settings.WHATSAPP_BASE_URL}/{credential.waba_id}/message_templates", token)
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

def _create_graph_template(waba_id: str, token: str, payload: dict[str, Any]) -> dict:
    endpoint = "upsert_message_templates" if str(payload.get("category")).upper() == "AUTHENTICATION" else "message_templates"
    return _post_graph(f"{settings.WHATSAPP_BASE_URL}/{waba_id}/{endpoint}", token, payload)

def _delete_graph_template(waba_id: str, token: str, name: str) -> dict:
    response = requests.delete(
        f"{settings.WHATSAPP_BASE_URL}/{waba_id}/message_templates",
        params={"name": name},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse_response(response)

def _get_graph(url: str, token: str, params: dict[str, Any] | None = None) -> dict:
    response = requests.get(url, params=params or {}, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT)
    return _parse_response(response)

def _post_graph(url: str, token: str, payload: dict[str, Any]) -> dict:
    logger.info("Meta POST request url=%s payload=%s", url, payload)
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
    logger.info("Meta API response status=%s body=%s", response.status_code, body)
    if not 200 <= response.status_code < 300:
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        logger.error("Meta API request failed status=%s body=%s", response.status_code, body)
        raise RuntimeError(message or f"Meta API request failed with {response.status_code}")
    return body if isinstance(body, dict) else {"data": body}

__all__ = [
    "sync_templates",
    "_create_graph_template",
    "_delete_graph_template",
    "_get_graph",
    "_post_graph",
    "_parse_response",
]
