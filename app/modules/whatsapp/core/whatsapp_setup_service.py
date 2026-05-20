import re
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.whatsapp import WhatsappCredential


REQUEST_TIMEOUT = 30


def serialize_whatsapp_credential(credential: WhatsappCredential) -> dict:
    return {
        "id": credential.id,
        "tenant_id": credential.tenant_id,
        "waba_id": credential.waba_id,
        "business_id": credential.business_id,
        "phone_number_id": credential.phone_number_id,
        "phone_number": credential.phone_number,
        "status": credential.status,
        "business_name": credential.business_name,
        "verified_name": credential.verified_name,
        "name": credential.name,
        "callback_url": credential.callback_url,
        "nerochat_callback_url": credential.nerochat_callback_url,
    }


def get_whatsapp_credential(
    db: Session,
    tenant_id: str = "default",
) -> WhatsappCredential | None:
    return db.execute(
        select(WhatsappCredential).where(WhatsappCredential.tenant_id == tenant_id)
    ).scalars().first()


def setup_whatsapp_business(
    db: Session,
    *,
    authorization_token: str,
    phone_number_id: str,
    waba_id: str,
    business_id: str,
    tenant_id: str = "default",
) -> WhatsappCredential:
    _require_meta_settings()

    existing = get_whatsapp_credential(db, tenant_id=tenant_id)
    if existing:
        raise ValueError("WhatsApp credential already exists")

    credential = WhatsappCredential(
        tenant_id=tenant_id,
        waba_id=str(waba_id),
        business_id=str(business_id),
        phone_number_id=str(phone_number_id),
        authorization_token=str(authorization_token),
        status="pending",
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    system_access_token = _exchange_authorization_code(authorization_token)
    credential.token = system_access_token
    db.commit()

    _register_phone_number(phone_number_id, system_access_token)

    business_info = _get_graph(
        f"{settings.whatsapp_base_url}/{waba_id}",
        system_access_token,
        params={"fields": "id,name,owner_business_info"},
    )
    credential.name = business_info.get("name")
    owner_info = business_info.get("owner_business_info") or {}
    credential.business_name = owner_info.get("name")
    db.commit()

    phone = _find_phone_number(waba_id, phone_number_id, system_access_token)
    credential.phone_number = _normalize_mobile(phone.get("display_phone_number"))
    credential.verified_name = phone.get("verified_name")
    credential.status = "active"
    db.commit()

    _subscribe_app(waba_id, system_access_token)

    callback_url = _register_webhook(waba_id, phone_number_id, system_access_token)
    if callback_url:
        credential.nerochat_callback_url = callback_url
        db.commit()

    db.refresh(credential)
    return credential


def _require_meta_settings() -> None:
    missing = [
        key
        for key, value in {
            "META_APP_ID": settings.meta_app_id,
            "META_APP_SECRET": settings.meta_app_secret,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing Meta settings: {', '.join(missing)}")


def _exchange_authorization_code(code: str) -> str:
    response = _post_graph(
        f"{settings.whatsapp_base_url}/oauth/access_token",
        token=None,
        data={
            "grant_type": "authorization_code",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "code": code,
        },
        content_type="application/x-www-form-urlencoded",
    )
    access_token = response.get("access_token")
    if not access_token:
        raise RuntimeError("Meta did not return an access token")
    return str(access_token)


def _register_phone_number(phone_number_id: str, token: str) -> None:
    _post_graph(
        f"{settings.whatsapp_base_url}/{phone_number_id}/register",
        token=token,
        data={"messaging_product": "whatsapp", "pin": "797711"},
    )


def _find_phone_number(waba_id: str, phone_number_id: str, token: str) -> dict[str, Any]:
    response = _get_graph(f"{settings.whatsapp_base_url}/{waba_id}/phone_numbers", token)
    for phone in response.get("data") or []:
        if str(phone.get("id")) == str(phone_number_id):
            return phone
    raise ValueError("Provided phone number not found in WABA")


def _subscribe_app(waba_id: str, token: str) -> None:
    _post_graph(f"{settings.whatsapp_base_url}/{waba_id}/subscribed_apps", token=token, data={})


def _register_webhook(waba_id: str, phone_number_id: str, token: str) -> str | None:
    if not settings.public_webhook_base_url:
        return None

    callback_url = (
        f"{settings.public_webhook_base_url.rstrip('/')}/webhook"
        f"?phone_number_id={phone_number_id}"
    )
    payload = {"override_callback_url": callback_url}
    if settings.verify_token:
        payload["verify_token"] = settings.verify_token

    _post_graph(
        f"{settings.whatsapp_base_url}/{waba_id}/subscribed_apps",
        token=token,
        data=payload,
    )
    return callback_url


def _get_graph(url: str, token: str, params: dict[str, Any] | None = None) -> dict:
    response = requests.get(
        url,
        params=params or {},
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    return _parse_graph_response(response)


def _post_graph(
    url: str,
    *,
    token: str | None,
    data: dict[str, Any],
    content_type: str = "application/json",
) -> dict:
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if content_type == "application/x-www-form-urlencoded":
        response = requests.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
    else:
        response = requests.post(url, json=data, headers=headers, timeout=REQUEST_TIMEOUT)
    return _parse_graph_response(response)


def _parse_graph_response(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text}

    if not 200 <= response.status_code < 300:
        message = body.get("error", {}).get("message") if isinstance(body, dict) else None
        raise RuntimeError(message or f"Meta API request failed with {response.status_code}")
    if not isinstance(body, dict):
        return {"data": body}
    return body


def _normalize_mobile(phone: str | None) -> str | None:
    if not phone:
        return None
    value = re.sub(r"\s+", "", phone)
    value = value.lstrip("+")
    value = re.sub(r"^0+", "", value)
    return value
