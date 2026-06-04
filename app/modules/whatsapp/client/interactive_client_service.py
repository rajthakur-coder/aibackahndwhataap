from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from app.config import settings
from app.modules.whatsapp.analytics.analytics_service import tracking_url
from app.modules.whatsapp.client.credentials import resolve_whatsapp_client_credentials


WHATSAPP_API_VERSION = "v25.0"
REQUEST_TIMEOUT = 20


def send_whatsapp_product_list(
    phone: str,
    products: list[dict],
    body_text: str,
    header_text: str = "Products",
    section_title: str = "Recommended",
    footer_text: str | None = None,
) -> dict:
    credentials = resolve_whatsapp_client_credentials()
    catalog_id = settings.WHATSAPP_CATALOG_ID

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
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
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
    credentials = resolve_whatsapp_client_credentials()

    if not phone:
        raise ValueError("Phone is required")

    cards = []
    for product in products:
        image_url = _carousel_image_url(product.get("image_url"))
        product_url = tracking_url(
            product.get("product_url"),
            phone=phone,
            source="carousel",
            title=str(product.get("title") or "Product").strip(),
        )
        if not image_url or not product_url:
            continue

        title = str(product.get("title") or "Product").strip()
        price = str(product.get("price") or product.get("price_min") or "").strip()
        body_parts = [f"*{title[:80]}*"]
        if price:
            body_parts.append(f"Price: {price}")

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
                    "text": "\n\n".join(body_parts)[:160],
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
        if len(cards) >= 10:
            break

    if len(cards) < 2:
        raise ValueError("Carousel requires at least 2 products with public image and product URL")

    url = (
        f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/"
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()

def _carousel_image_url(image_url: str | None) -> str | None:
    if not image_url:
        return None

    image_url = str(image_url).strip()
    parsed = urlparse(image_url)
    if "cdn.shopify.com" not in parsed.netloc.lower():
        return image_url

    path = parsed.path.lower()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("width", "800")
    if path.endswith(".png"):
        query.setdefault("format", "jpg")

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )

def send_whatsapp_cta_url(
    phone: str,
    body_text: str,
    button_text: str,
    button_url: str,
    header_text: str | None = None,
    image_url: str | None = None,
    footer_text: str | None = None,
) -> dict:
    credentials = resolve_whatsapp_client_credentials()

    if not phone or not body_text or not button_text or not button_url:
        raise ValueError("Phone, body text, button text, and button URL are required")
    tracked_button_url = tracking_url(
        button_url,
        phone=phone,
        source="cta_url",
        title=button_text,
    )

    interactive = {
        "type": "cta_url",
        "body": {"text": body_text[:1024]},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": button_text[:20],
                "url": tracked_button_url,
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
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
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
    credentials = resolve_whatsapp_client_credentials()

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
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
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
    credentials = resolve_whatsapp_client_credentials()

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
        f"{credentials.phone_number_id}/messages"
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
            "Authorization": f"Bearer {credentials.access_token}",
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

__all__ = [
    "send_whatsapp_product_list",
    "send_whatsapp_carousel",
    "send_whatsapp_cta_url",
    "send_whatsapp_list",
    "send_whatsapp_reply_buttons",
    "_product_retailer_id",
]
