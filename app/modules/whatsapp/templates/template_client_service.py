import requests

from app.modules.whatsapp.client.credentials import resolve_whatsapp_client_credentials


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


def send_whatsapp_template(
    phone: str,
    template_name: str,
    language: str = "en",
    body_parameters: list[str] | None = None,
    button_url_parameters: list[str] | None = None,
) -> dict:
    credentials = resolve_whatsapp_client_credentials()

    if not phone or not template_name:
        raise ValueError("Phone and template name are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
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
