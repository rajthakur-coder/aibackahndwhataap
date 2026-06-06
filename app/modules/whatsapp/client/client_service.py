import requests

from app.modules.whatsapp.client.credentials import resolve_whatsapp_client_credentials


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


from app.modules.whatsapp.client.interactive_client_service import *
from app.modules.whatsapp.templates.template_client_service import *

def mark_whatsapp_message_read_with_typing(message_id: str, tenant_id: str | None = None) -> dict:
    credentials = resolve_whatsapp_client_credentials(tenant_id=tenant_id)

    if not message_id:
        raise ValueError("Message ID is required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{credentials.phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
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


def send_whatsapp_message(phone: str, message: str, tenant_id: str | None = None) -> dict:
    credentials = resolve_whatsapp_client_credentials(tenant_id=tenant_id)

    if not phone or not message:
        raise ValueError("Phone and message are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{credentials.phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message[:4096]},
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


def send_whatsapp_image(
    phone: str,
    image_url: str,
    caption: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    credentials = resolve_whatsapp_client_credentials(tenant_id=tenant_id)

    if not phone or not image_url:
        raise ValueError("Phone and image URL are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{credentials.phone_number_id}/messages"
    )

    image = {"link": image_url}
    if caption:
        image["caption"] = caption[:1024]

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "image",
        "image": image,
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
