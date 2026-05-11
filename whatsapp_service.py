import os

import requests


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


def send_whatsapp_message(phone: str, message: str) -> dict:
    access_token = os.getenv("ACCESS_TOKEN")
    phone_number_id = os.getenv("PHONE_NUMBER_ID")

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
