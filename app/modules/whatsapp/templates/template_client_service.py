import requests

from app.config import settings
from app.modules.whatsapp.analytics.analytics_service import tracking_url


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


def send_whatsapp_template(
    phone: str,
    template_name: str,
    language: str = "en",
    body_parameters: list[str] | None = None,
    button_url_parameters: list[str] | None = None,
) -> dict:
    access_token = settings.ACCESS_TOKEN
    phone_number_id = settings.PHONE_NUMBER_ID

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")

    if not phone or not template_name:
        raise ValueError("Phone and template name are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    template = {
        "name": template_name,
        "language": {"code": language or "en"},
    }
    if body_parameters:
        template.setdefault("components", []).append(
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(value)[:1024]}
                    for value in body_parameters
                ],
            }
        )
    for index, value in enumerate(button_url_parameters or []):
        template.setdefault("components", []).append(
            {
                "type": "button",
                "sub_type": "url",
                "index": str(index),
                "parameters": [{"type": "text", "text": str(value)[:1024]}],
            }
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": template,
    }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

__all__ = [
    "send_whatsapp_template",
]
