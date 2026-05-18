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


def send_whatsapp_image(phone: str, image_url: str, caption: str | None = None) -> dict:
    access_token = os.getenv("ACCESS_TOKEN")
    phone_number_id = os.getenv("PHONE_NUMBER_ID")

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


def send_whatsapp_product_list(
    phone: str,
    products: list[dict],
    body_text: str,
    header_text: str = "Products",
    section_title: str = "Recommended",
    footer_text: str | None = None,
) -> dict:
    access_token = os.getenv("ACCESS_TOKEN")
    phone_number_id = os.getenv("PHONE_NUMBER_ID")
    catalog_id = os.getenv("WHATSAPP_CATALOG_ID")

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    if not catalog_id:
        raise RuntimeError("WhatsApp catalog ID is not configured")
    if not phone:
        raise ValueError("Phone is required")

    product_items = []
    for product in products[:10]:
        retailer_id = _product_retailer_id(product)
        if retailer_id:
            product_items.append({"product_retailer_id": retailer_id})

    if not product_items:
        raise ValueError("Products do not have WhatsApp catalog retailer IDs")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    interactive = {
        "type": "product_list",
        "header": {"type": "text", "text": header_text[:60]},
        "body": {"text": body_text[:1024]},
        "action": {
            "catalog_id": catalog_id,
            "sections": [
                {
                    "title": section_title[:24],
                    "product_items": product_items,
                }
            ],
        },
    }
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": interactive,
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


def send_whatsapp_template(
    phone: str,
    template_name: str,
    language: str = "en",
    body_parameters: list[str] | None = None,
) -> dict:
    access_token = os.getenv("ACCESS_TOKEN")
    phone_number_id = os.getenv("PHONE_NUMBER_ID")

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
        template["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(value)[:1024]}
                    for value in body_parameters
                ],
            }
        ]

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


def _product_retailer_id(product: dict) -> str | None:
    for key in (
        "retailer_id",
        "product_retailer_id",
        "sku",
        "external_id",
        "shopify_product_id",
    ):
        value = product.get(key)
        if value:
            return str(value).strip()
    return None

