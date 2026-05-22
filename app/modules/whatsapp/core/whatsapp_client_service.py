import requests

from app.config import settings


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


def send_whatsapp_message(phone: str, message: str) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

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
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

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
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id
    catalog_id = settings.whatsapp_catalog_id

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


def send_whatsapp_carousel(
    phone: str,
    products: list[dict],
    body_text: str,
    button_text: str = "Buy now",
) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    if not phone:
        raise ValueError("Phone is required")

    cards = []
    for product in products:
        image_url = product.get("image_url")
        product_url = product.get("product_url")
        if not image_url or not product_url:
            continue

        title = str(product.get("title") or "Product").strip()
        price = str(product.get("price") or product.get("price_min") or "").strip()
        description = str(product.get("description") or product.get("caption") or "").strip()
        body_parts = [f"*{title[:80]}*"]
        if price:
            body_parts.append(f"Price: {price}")
        if description and description != title:
            body_parts.append(description[:90])

        card_index = len(cards)
        cards.append(
            {
                "card_index": card_index,
                "type": "cta_url",
                "header": {
                    "type": "image",
                    "image": {"link": image_url},
                },
                "body": {
                    "text": "\n".join(body_parts)[:160],
                },
                "action": {
                    "name": "cta_url",
                    "parameters": {
                        "display_text": button_text[:20],
                        "url": product_url,
                    },
                },
            }
        )
        if len(cards) >= 5:
            break

    if len(cards) < 2:
        raise ValueError("Carousel requires at least 2 products with public image and product URL")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "carousel",
            "body": {"text": body_text[:1024]},
            "action": {"cards": cards},
        },
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


def send_whatsapp_cta_url(
    phone: str,
    body_text: str,
    button_text: str,
    button_url: str,
    header_text: str | None = None,
    image_url: str | None = None,
    footer_text: str | None = None,
) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    if not phone or not body_text or not button_text or not button_url:
        raise ValueError("Phone, body text, button text, and button URL are required")

    interactive = {
        "type": "cta_url",
        "body": {"text": body_text[:1024]},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": button_text[:20],
                "url": button_url,
            },
        },
    }
    if image_url:
        interactive["header"] = {
            "type": "image",
            "image": {"link": image_url},
        }
    elif header_text:
        interactive["header"] = {
            "type": "text",
            "text": header_text[:60],
        }
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
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


def send_whatsapp_list(
    phone: str,
    body_text: str,
    button_text: str,
    rows: list[dict],
    header_text: str | None = None,
    section_title: str = "Options",
    footer_text: str | None = None,
) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    if not phone or not body_text or not button_text or not rows:
        raise ValueError("Phone, body text, button text, and rows are required")

    action_rows = []
    for row in rows[:10]:
        row_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not row_id or not title:
            continue
        item = {
            "id": row_id[:200],
            "title": title[:24],
        }
        description = str(row.get("description") or "").strip()
        if description:
            item["description"] = description[:72]
        action_rows.append(item)

    if not action_rows:
        raise ValueError("At least one valid list row is required")

    interactive = {
        "type": "list",
        "body": {"text": body_text[:4096]},
        "action": {
            "button": button_text[:20],
            "sections": [
                {
                    "title": section_title[:24],
                    "rows": action_rows,
                }
            ],
        },
    }
    if header_text:
        interactive["header"] = {
            "type": "text",
            "text": header_text[:60],
        }
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
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


def send_whatsapp_reply_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],
    header_text: str | None = None,
    footer_text: str | None = None,
    image_url: str | None = None,
) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

    if not access_token or not phone_number_id:
        raise RuntimeError("WhatsApp credentials are not configured")
    if not phone or not body_text or not buttons:
        raise ValueError("Phone, body text, and buttons are required")

    action_buttons = []
    for button in buttons[:3]:
        button_id = str(button.get("id") or "").strip()
        title = str(button.get("title") or "").strip()
        if not button_id or not title:
            continue
        action_buttons.append(
            {
                "type": "reply",
                "reply": {
                    "id": button_id[:256],
                    "title": title[:20],
                },
            }
        )

    if not action_buttons:
        raise ValueError("At least one valid reply button is required")

    interactive = {
        "type": "button",
        "body": {"text": body_text[:1024]},
        "action": {"buttons": action_buttons},
    }
    if image_url:
        interactive["header"] = {
            "type": "image",
            "image": {"link": image_url},
        }
    elif header_text:
        interactive["header"] = {
            "type": "text",
            "text": header_text[:60],
        }
    if footer_text:
        interactive["footer"] = {"text": footer_text[:60]}

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
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
    button_url_parameters: list[str] | None = None,
) -> dict:
    access_token = settings.access_token
    phone_number_id = settings.phone_number_id

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

