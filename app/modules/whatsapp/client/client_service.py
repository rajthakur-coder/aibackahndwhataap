import requests

from app.config import settings
from app.modules.whatsapp.analytics.analytics_service import tracking_url


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


from app.modules.whatsapp.client.interactive_client_service import *
from app.modules.whatsapp.templates.template_client_service import *

def mark_whatsapp_message_read_with_typing(message_id: str) -> dict:
    access_token = settings.ACCESS_TOKEN
    phone_number_id = settings.PHONE_NUMBER_ID

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")

    if not message_id:
        raise ValueError("Message ID is required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
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
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def send_whatsapp_message(phone: str, message: str) -> dict:
    access_token = settings.ACCESS_TOKEN
    phone_number_id = settings.PHONE_NUMBER_ID

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")

    if not phone or not message:
        raise ValueError("Phone and message are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
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
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def send_whatsapp_image(phone: str, image_url: str, caption: str | None = None) -> dict:
    access_token = settings.ACCESS_TOKEN
    phone_number_id = settings.PHONE_NUMBER_ID

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")

    if not phone or not image_url:
        raise ValueError("Phone and image URL are required")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
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
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()
