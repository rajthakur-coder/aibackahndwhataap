from app.models.ecommerce import EcommerceConnection
from app.modules.ecommerce.providers.shopify.http_client import _shopify_request


def create_shopify_draft_order(
    connection: EcommerceConnection,
    *,
    line_items: list[dict],
    phone: str,
    email: str | None = None,
    discount: dict | None = None,
    note: str | None = None,
) -> dict:
    payload_items = []
    for item in line_items:
        quantity = max(1, int(item.get("qty") or item.get("quantity") or 1))
        variant_id = str(item.get("variant_id") or "").strip()
        if variant_id:
            payload_items.append({"variant_id": int(variant_id), "quantity": quantity})
            continue
        payload_items.append(
            {
                "title": item.get("title") or item.get("sku") or "WhatsApp cart item",
                "price": str(item.get("price") or "0"),
                "quantity": quantity,
            }
        )

    draft_order = {
        "line_items": payload_items,
        "note": note or "Created from WhatsApp AI cart",
        "tags": "whatsapp,ai-agent",
    }
    if email:
        draft_order["email"] = email
    if phone:
        draft_order["phone"] = phone
    applied_discount = _shopify_discount_payload(discount)
    if applied_discount:
        draft_order["applied_discount"] = applied_discount

    response = _shopify_request("POST", connection, "/draft_orders.json", payload={"draft_order": draft_order})
    return response.json().get("draft_order") or {}


def update_shopify_order_return_note(
    connection: EcommerceConnection,
    *,
    order_id: str,
    note: str,
    tags: str = "return-requested,whatsapp",
) -> dict:
    response = _shopify_request(
        "PUT",
        connection,
        f"/orders/{order_id}.json",
        payload={"order": {"id": int(order_id), "note": note, "tags": tags}},
    )
    return response.json().get("order") or {}


def _shopify_discount_payload(discount: dict | None) -> dict | None:
    if not discount:
        return None
    value_type = str(discount.get("value_type") or discount.get("type") or "").lower()
    value = discount.get("value")
    if value in (None, ""):
        return None
    if value_type in {"percentage", "percent"}:
        return {
            "description": discount.get("code") or "WhatsApp discount",
            "value_type": "percentage",
            "value": str(value),
        }
    if value_type in {"fixed_amount", "fixed"}:
        return {
            "description": discount.get("code") or "WhatsApp discount",
            "value_type": "fixed_amount",
            "value": str(value),
        }
    if value_type == "free_shipping":
        return None
    return None


__all__ = ["create_shopify_draft_order", "update_shopify_order_return_note"]
