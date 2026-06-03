from __future__ import annotations

import json
import re
from collections import defaultdict

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.models.whatsapp import Message
from app.modules.whatsapp.messages.messages_service import save_message
from app.modules.whatsapp.client.interactive_client_service import (
    send_whatsapp_cta_url,
    send_whatsapp_list,
    send_whatsapp_reply_buttons,
)
from app.modules.ai.orchestrator.tool_executor import execute_tool
from app.modules.ecommerce.orders.order_service import find_order_for_customer, list_recent_orders_for_customer
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config
from app.modules.whatsapp.webhooks.responses.catalog_service import try_send_catalog_category_list
from app.modules.whatsapp.webhooks.responses.menu_service import main_menu_buttons

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.whatsapp.webhooks.processing.pipeline import WebhookProcessingContext


WELCOME_BUTTONS = [
    {"id": "shop", "title": "Shop / Browse"},
    {"id": "track", "title": "Track Order"},
    {"id": "return", "title": "Return / Exchange"},
]
RETURN_OUTCOME_BUTTONS = [
    {"id": "return:refund", "title": "Refund"},
    {"id": "return:exchange", "title": "Exchange"},
    {"id": "return:credit", "title": "Store credit"},
]
GIFT_TIMELINE_BUTTONS = [
    {"id": "gift_time:<2w", "title": "<2 weeks"},
    {"id": "gift_time:2-4w", "title": "2-4 weeks"},
    {"id": "gift_time:flex", "title": "Flexible"},
]
RETURN_REASON_ROWS = [
    {"id": "return:damaged", "title": "Damaged", "description": "Damaged or defective"},
    {"id": "return:wrong", "title": "Wrong product", "description": "Different item received"},
    {"id": "return:style", "title": "Doesn't suit", "description": "Color, size, or feel"},
    {"id": "return:changed", "title": "Changed mind", "description": "No longer needed"},
]
GIFTING_ROWS = [
    {"id": "gift:corporate", "title": "Corporate", "description": "Employee or client gifting"},
    {"id": "gift:wedding", "title": "Wedding/event", "description": "Wedding or event favors"},
    {"id": "gift:hospitality", "title": "Hospitality", "description": "Hotel or restaurant"},
    {"id": "gift:personal", "title": "Large order", "description": "Personal large order"},
]


async def _handle_commerce_interactive_flows(context: WebhookProcessingContext) -> bool:
    text = (context.text or context.query_text or "").strip().lower()
    if _is_welcome_request(text):
        return await _send_welcome(context)
    if _is_shop_request(text):
        return await _send_shop_list(context)
    if _is_track_request(text):
        return await _send_track_status_or_prompt(context)
    if _is_return_order_selection(text):
        return await _send_return_item_or_reason_list(context)
    if _is_return_item_selection(text):
        return await _send_return_reason_list(context)
    if _is_manual_return_order_id(context, text):
        return await _send_return_item_or_reason_list(context)
    if _is_return_request(text):
        return await _send_return_order_or_reason_list(context)
    if _is_return_confirmation_yes(text):
        return await _send_return_eligibility_result(context, confirmed=True)
    if _is_return_confirmation_no(text):
        await _send_text(
            context,
            _flow_text(
                context,
                "return_cancelled_message",
                "Okay, I have not started the return. Anything else I can help with?",
            ),
        )
        return True
    if _is_gifting_request(text):
        return await _send_gifting_list(context)
    if _is_return_reason(text):
        return await _send_return_outcome_buttons(context)
    if _is_gifting_occasion(text):
        return await _send_gifting_quantity_buttons(context)
    if _is_gifting_quantity(text):
        return await _send_gifting_timeline_buttons(context)
    if _is_gifting_timeline(text):
        return await _send_gifting_email_prompt(context)
    if _is_gifting_contact_response(context, text):
        return await _send_gifting_contact_ack(context)
    if _is_return_outcome(text):
        return await _send_return_confirmation(context)
    return False


async def _send_welcome(context: WebhookProcessingContext) -> bool:
    brand_name = _brand_name(context)
    body = str(getattr(context.bot_settings, "welcome_message", "") or "").strip() or (
        f"Hi. Welcome to {brand_name}.\n\n"
        "I can help with products, orders, returns, or gifting.\n"
        "Pick one, or just type your question."
    )
    buttons = main_menu_buttons(context.bot_settings)
    try:
        await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, brand_name[:20])
        save_message(
            context.db,
            context.phone,
            "[buttons] Brand welcome",
            "outgoing",
            message_type="buttons",
            payload={"title": "Brand welcome", "body": body, "buttons": buttons},
        )
        return True
    except Exception:
        await _send_text(context, body)
        return True


async def _send_shop_list(context: WebhookProcessingContext) -> bool:
    orders = list_recent_orders_for_customer(context.db, context.phone, limit=1, tenant_id=context.tenant_id)
    if orders:
        last = orders[0]
        item_name = _first_order_item_name(last) or "your last pick"
        body = _flow_text(
            context,
            "returning_shopper_message",
            "Welcome back.\n\nLast time: {item_name}.\n\nWant to reorder, see best sellers, or browse the catalog?",
            item_name=item_name,
        )
        buttons = _flow_buttons(
            context,
            "returning_shopper_buttons",
            [
                {"id": f"reorder:{last.order_number}", "title": "Reorder now"},
                {"id": "catalog:best_sellers", "title": "Best sellers"},
                {"id": "menu:catalog", "title": "Browse"},
            ],
            order_number=last.order_number,
        )
        try:
            await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Shop")
            save_message(context.db, context.phone, "[buttons] Returning shopper", "outgoing", message_type="buttons", payload={"title": "Returning shopper", "body": body, "buttons": buttons})
            return True
        except Exception:
            await _send_text(context, body)
            return True

    await _send_text(context, _first_time_offer_text(context))
    if await try_send_catalog_category_list(
        context.db,
        context.phone,
        context.reply_language,
        timing=context.timing,
    ):
        return True

    await _send_text(
        context,
        _flow_text(
            context,
            "catalog_unavailable_message",
            "I could not load the store categories right now. Try asking for best sellers or all products.",
        ),
    )
    return True


async def _send_track_status_or_prompt(context: WebhookProcessingContext) -> bool:
    result = execute_tool(
        context.db,
        "get_order_status",
        phone=context.phone,
        message=context.query_text,
        entities=getattr(context.understanding, "entities", {}),
        tenant_id=context.tenant_id,
    )
    if result.status == "success" and isinstance(result.data, dict):
        dispatch = execute_tool(
            context.db,
            "get_dispatch_details",
            phone=context.phone,
            message=context.query_text,
            entities=getattr(context.understanding, "entities", {}),
            tenant_id=context.tenant_id,
        )
        dispatch_data = dispatch.data if dispatch.status == "success" and isinstance(dispatch.data, dict) else {}
        await _send_order_status(context, {**result.data, **dispatch_data})
        return True
    body = _flow_text(
        context,
        "order_id_prompt",
        "Sure. Drop your order ID, like #1234, or the phone used for the order.",
    )
    await _send_text(context, body)
    return True


async def _send_order_status(context: WebhookProcessingContext, data: dict) -> None:
    items = data.get("items") or []
    item_count = len(items) if isinstance(items, list) else 0
    lines = [
        f"Order {data.get('order_number') or data.get('id')} · {item_count or 'your'} item{'s' if item_count != 1 else ''}",
        "",
        f"Status: {data.get('delivery_status') or data.get('shipment_status') or data.get('fulfillment_status') or data.get('status') or 'Received'}",
    ]
    if data.get("courier_company"):
        lines.append(f"Courier: {data.get('courier_company')}")
    if data.get("tracking_number"):
        lines.append(f"AWB: {data.get('tracking_number')}")
    if data.get("tracking_url"):
        lines.append(f"Tracking: {data.get('tracking_url')}")
    await _send_text(context, "\n".join(lines[:6]))


async def _send_return_order_or_reason_list(context: WebhookProcessingContext) -> bool:
    orders = list_recent_orders_for_customer(context.db, context.phone, limit=3, tenant_id=context.tenant_id)
    if orders:
        rows = [
            {
                "id": f"return_order:{order.order_number}",
                "title": str(order.order_number)[:24],
                "description": _first_order_item_name(order) or str(order.updated_at or "")[:72],
            }
            for order in orders
        ]
        body = _flow_text(context, "return_order_prompt", "Sorry it did not work out. Which order?")
        try:
            await run_in_threadpool(send_whatsapp_list, context.phone, body, "Orders", rows, "Return", "Orders")
            save_message(context.db, context.phone, "[list] Return orders", "outgoing", message_type="list", payload={"title": "Return orders", "body": body, "rows": rows})
            return True
        except Exception:
            pass
    await _send_text(
        context,
        _flow_text(
            context,
            "return_order_id_prompt",
            "I could not find a recent order on this WhatsApp number. Please share your order ID, like #5967.",
        ),
    )
    return True


async def _send_return_item_or_reason_list(context: WebhookProcessingContext) -> bool:
    state = _return_flow_state(context)
    order_id = state.get("order_id")
    order = find_order_for_customer(context.db, context.phone, order_id, tenant_id=context.tenant_id) if order_id else None
    if order_id and not order:
        await _send_text(
            context,
            _flow_text(
                context,
                "return_order_not_found",
                "I could not find order {order_id}. Please check the order ID or share another one.",
                order_id=order_id,
            ),
        )
        return True
    items = _order_items(order) if order else []
    if len(items) <= 1:
        return await _send_return_reason_list(context)

    rows = [
        {
            "id": f"return_item:{order.order_number}:{index}",
            "title": str(item.get("name") or item.get("title") or item.get("sku") or f"Item {index + 1}")[:24],
            "description": f"Qty {item.get('quantity') or 1}",
        }
        for index, item in enumerate(items[:10])
    ]
    body = _flow_text(context, "return_item_prompt", "Which item do you want to return or exchange?")
    try:
        await run_in_threadpool(send_whatsapp_list, context.phone, body, "Items", rows, "Return", "Items")
        save_message(context.db, context.phone, "[list] Return items", "outgoing", message_type="list", payload={"title": "Return items", "body": body, "rows": rows})
        return True
    except Exception:
        return await _send_return_reason_list(context)


async def _send_return_reason_list(context: WebhookProcessingContext) -> bool:
    body = _flow_text(context, "return_reason_prompt", "Sorry it did not work out. What went wrong?")
    try:
        await run_in_threadpool(
            send_whatsapp_list,
            context.phone,
            body,
            "Reasons",
            RETURN_REASON_ROWS,
            "Return",
            "Reason",
        )
        save_message(context.db, context.phone, "[list] Return reasons", "outgoing", message_type="list", payload={"title": "Return reasons", "body": body, "rows": RETURN_REASON_ROWS})
        return True
    except Exception:
        await _send_text(
            context,
            _flow_text(
                context,
                "return_reason_fallback",
                "What went wrong: damaged, wrong product, color/size, or changed mind?",
            ),
        )
        return True


async def _send_return_outcome_buttons(context: WebhookProcessingContext) -> bool:
    body = _flow_text(
        context,
        "return_outcome_prompt",
        "What would you prefer: refund, exchange, or store credit?",
    )
    buttons = _flow_buttons(context, "return_outcome_buttons", RETURN_OUTCOME_BUTTONS)
    try:
        await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Return options")
        save_message(context.db, context.phone, "[buttons] Return outcome", "outgoing", message_type="buttons", payload={"title": "Return outcome", "body": body, "buttons": buttons})
        return True
    except Exception:
        await _send_text(context, body)
        return True


async def _send_return_confirmation(context: WebhookProcessingContext) -> bool:
    state = _return_flow_state(context)
    summary = _return_summary_text(context, state)
    body = _flow_text(
        context,
        "return_confirmation_prompt",
        "{summary}\n\nI can check return eligibility and log the request. Should I continue?",
        summary=summary,
    )
    try:
        await run_in_threadpool(
            send_whatsapp_reply_buttons,
            context.phone,
            body,
            [{"id": "confirm:return:yes", "title": "Yes"}, {"id": "confirm:return:no", "title": "No"}],
            "Confirm return",
        )
        save_message(context.db, context.phone, "[buttons] Confirm return", "outgoing", message_type="buttons", payload={"title": "Confirm return", "body": body})
        return True
    except Exception:
        await _send_text(
            context,
            _flow_text(
                context,
                "return_confirmation_fallback",
                "{body} Reply Yes or No.",
                body=body,
            ),
        )
        return True


async def _send_return_eligibility_result(context: WebhookProcessingContext, confirmed: bool) -> bool:
    state = _return_flow_state(context)
    result = execute_tool(
        context.db,
        "initiate_return" if confirmed else "get_return_eligibility",
        phone=context.phone,
        message="confirm:return:yes" if confirmed else context.query_text,
        entities={**getattr(context.understanding, "entities", {}), **state, "confirmed": confirmed},
        tenant_id=context.tenant_id,
    )
    data = result.data if isinstance(result.data, dict) else {}
    eligibility = data.get("eligibility") if confirmed else data
    if result.status == "needs_input":
        await _send_text(context, result.message)
        return True
    if eligibility and eligibility.get("eligible") and not confirmed:
        body = _flow_text(
            context,
            "return_outcome_prompt",
            "You're within the return window.\n\nWant a refund, exchange, or store credit with a 5% bonus?",
        )
        buttons = _flow_buttons(context, "return_outcome_buttons", RETURN_OUTCOME_BUTTONS)
        try:
            await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Return options")
            save_message(context.db, context.phone, "[buttons] Return outcome", "outgoing", message_type="buttons", payload={"title": "Return outcome", "body": body, "buttons": buttons})
            return True
        except Exception:
            await _send_text(context, body)
            return True
    await _send_text(context, result.message)
    return True


async def _send_gifting_list(context: WebhookProcessingContext) -> bool:
    body = _flow_text(context, "gifting_occasion_prompt", "Lovely. What is the occasion?")
    try:
        await run_in_threadpool(
            send_whatsapp_list,
            context.phone,
            body,
            "Occasion",
            GIFTING_ROWS,
            "Gifting",
            "Occasion",
        )
        save_message(context.db, context.phone, "[list] Gifting occasion", "outgoing", message_type="list", payload={"title": "Gifting", "body": body, "rows": GIFTING_ROWS})
        return True
    except Exception:
        await _send_text(
            context,
            _flow_text(
                context,
                "gifting_occasion_fallback",
                "Corporate gifting, wedding/event, hospitality, or personal large order?",
            ),
        )
        return True


async def _send_gifting_quantity_buttons(context: WebhookProcessingContext) -> bool:
    body = _flow_text(context, "gifting_quantity_prompt", "Quantity?")
    buttons = _flow_buttons(
        context,
        "gifting_quantity_buttons",
        [{"id": "gift_qty:<25", "title": "<25"}, {"id": "gift_qty:25-100", "title": "25-100"}, {"id": "gift_qty:100+", "title": "100+"}],
    )
    try:
        await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Gifting")
        save_message(context.db, context.phone, "[buttons] Gifting quantity", "outgoing", message_type="buttons", payload={"title": "Gifting quantity", "body": body, "buttons": buttons})
        return True
    except Exception:
        await _send_text(context, _flow_text(context, "gifting_quantity_fallback", "Quantity: <25, 25-100, 100+?"))
        return True


async def _send_gifting_timeline_buttons(context: WebhookProcessingContext) -> bool:
    body = _flow_text(context, "gifting_timeline_prompt", "Timeline?")
    buttons = _flow_buttons(context, "gifting_timeline_buttons", GIFT_TIMELINE_BUTTONS)
    try:
        await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Gifting")
        save_message(context.db, context.phone, "[buttons] Gifting timeline", "outgoing", message_type="buttons", payload={"title": "Gifting timeline", "body": body, "buttons": buttons})
        return True
    except Exception:
        await _send_text(context, _flow_text(context, "gifting_timeline_fallback", "Timeline: <2 weeks, 2-4 weeks, or flexible?"))
        return True


async def _send_gifting_email_prompt(context: WebhookProcessingContext) -> bool:
    body = _flow_text(
        context,
        "gifting_email_prompt",
        "Based on this, curated sets can work well.\n\n"
        "Drop your name and email, and I will log a proposal request for gifting.",
    )
    await _send_text(context, body)
    return True


async def _send_gifting_contact_ack(context: WebhookProcessingContext) -> bool:
    body = _flow_text(
        context,
        "gifting_contact_ack",
        "Thanks. I have noted your details and our team will follow up for the gifting proposal.",
    )
    await _send_text(context, body)
    return True


async def send_checkout_cta(context: WebhookProcessingContext, checkout_url: str) -> bool:
    body = _flow_text(context, "checkout_cta_message", "Cart ready. Tap to checkout.")
    button_text = _flow_text(context, "checkout_cta_button", "Checkout")
    try:
        await run_in_threadpool(send_whatsapp_cta_url, context.phone, body, button_text[:20], checkout_url, button_text[:20])
        save_message(context.db, context.phone, "[cta] Checkout", "outgoing", message_type="cta_url", payload={"title": button_text[:20], "body": body, "url": checkout_url})
        return True
    except Exception:
        return False


async def send_bundle_push(context: WebhookProcessingContext, products: list[dict]) -> bool:
    if not products:
        return False
    first = products[0]
    product_title = first.get("title") or "a matching product"
    body = _flow_text(
        context,
        "bundle_push_message",
        "Good pick. Want to pair it with {product_title}?",
        product_title=product_title,
    )
    buttons = _flow_buttons(
        context,
        "bundle_push_buttons",
        [{"id": "bundle:add", "title": "Add bundle"}, {"id": "bundle:skip", "title": "Just this"}, {"id": "checkout:start", "title": "Checkout"}],
    )
    try:
        await run_in_threadpool(send_whatsapp_reply_buttons, context.phone, body, buttons, "Complete the set")
        save_message(context.db, context.phone, "[buttons] Bundle push", "outgoing", message_type="buttons", payload={"title": "Bundle push", "body": body, "products": products[:3]})
        return True
    except Exception:
        return False


def _is_shop_request(text: str) -> bool:
    return text in {"shop / browse", "shop", "browse", "view catalog", "menu:catalog"} or text.startswith("shop:")


def _is_track_request(text: str) -> bool:
    return text in {"track order", "track", "order status", "menu:order_status"}


def _is_return_request(text: str) -> bool:
    return text in {"return / exchange", "return / exchanges", "return", "exchange"} or text.startswith("return_order:")


def _is_return_order_selection(text: str) -> bool:
    return text.startswith("return_order:")


def _is_return_item_selection(text: str) -> bool:
    return text.startswith("return_item:")


def _is_return_reason(text: str) -> bool:
    return text in {
        "damaged",
        "wrong product",
        "doesn't suit",
        "changed mind",
        "return:damaged",
        "return:wrong",
        "return:style",
        "return:changed",
    }


def _is_gifting_request(text: str) -> bool:
    return "gifting" in text or "bulk" in text or text.startswith("gift:")


def _is_gifting_occasion(text: str) -> bool:
    return text in {
        "corporate",
        "wedding/event",
        "hospitality",
        "large order",
        "gift:corporate",
        "gift:wedding",
        "gift:hospitality",
        "gift:personal",
    }


def _is_gifting_quantity(text: str) -> bool:
    return text in {"<25", "25-100", "100+", "500+", "gift_qty:<25", "gift_qty:25-100", "gift_qty:100+"}


def _is_welcome_request(text: str) -> bool:
    return text in {"hi", "hii", "hello", "hey", "start", "/start", "menu", "help"}


def _is_return_confirmation_yes(text: str) -> bool:
    return text in {"confirm:return:yes", "yes", "yes return", "yes, process return"}


def _is_return_confirmation_no(text: str) -> bool:
    return text in {"confirm:return:no", "no", "not now"}


def _is_return_outcome(text: str) -> bool:
    return text in {"return:refund", "refund", "return:exchange", "exchange", "return:credit", "store credit"}


def _is_manual_return_order_id(context: WebhookProcessingContext, text: str) -> bool:
    return _looks_like_order_id(text) and _latest_return_context_active(context)


def _looks_like_order_id(text: str) -> bool:
    return bool(re.fullmatch(r"#?[A-Za-z0-9][A-Za-z0-9-]{2,}", text or ""))


def _message_is_in_return_context(context: WebhookProcessingContext, message_id: int) -> bool:
    row = context.db.execute(
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.phone == context.phone,
            Message.direction == "outgoing",
            Message.message_type.in_(["buttons", "list"]),
            Message.payload.is_not(None),
            Message.id < message_id,
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).scalars().first()
    if not row:
        return False
    if not row.payload:
        return _looks_like_return_prompt(row.message)
    try:
        payload = json.loads(row.payload)
    except json.JSONDecodeError:
        return _looks_like_return_prompt(row.message)
    title = str(payload.get("title") or "").strip().lower() if isinstance(payload, dict) else ""
    return title in {"return reasons", "return orders", "return", "reason"} or _looks_like_return_prompt(row.message)


def _latest_return_context_active(context: WebhookProcessingContext) -> bool:
    row = context.db.execute(
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.phone == context.phone,
            Message.direction == "outgoing",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).scalars().first()
    if not row:
        return False
    if _looks_like_return_prompt(row.message):
        return True
    if not row.payload:
        return False
    try:
        payload = json.loads(row.payload)
    except json.JSONDecodeError:
        return False
    title = str(payload.get("title") or "").strip().lower() if isinstance(payload, dict) else ""
    return title in {"return reasons", "return orders", "return", "reason"}


def _looks_like_return_prompt(message: str | None) -> bool:
    lowered = str(message or "").lower()
    return (
        "return" in lowered
        and (
            "order id" in lowered
            or "which order" in lowered
            or "recent order" in lowered
            or "share your order" in lowered
        )
    )


def _is_gifting_timeline(text: str) -> bool:
    return text in {"gift_time:<2w", "gift_time:2-4w", "gift_time:flex", "<2 weeks", "2-4 weeks", "flexible"}


def _is_gifting_contact_response(context: WebhookProcessingContext, text: str) -> bool:
    if "@" not in text:
        return False
    title = _latest_interactive_title(context)
    return title in {"gifting timeline", "gifting"}


def _latest_interactive_title(context: WebhookProcessingContext) -> str:
    row = context.db.execute(
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.phone == context.phone,
            Message.direction == "outgoing",
            Message.message_type.in_(["buttons", "list"]),
            Message.payload.is_not(None),
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).scalars().first()
    if not row or not row.payload:
        return ""
    try:
        payload = json.loads(row.payload)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("title") or "").strip().lower() if isinstance(payload, dict) else ""


def _first_order_item_name(order) -> str | None:
    items = _order_items(order)
    if not items:
        return None
    first = items[0] if isinstance(items[0], dict) else {}
    return first.get("name") or first.get("title") or first.get("sku")


def _order_items(order) -> list[dict]:
    if not order:
        return []
    try:
        items = json.loads(order.items or "[]")
    except json.JSONDecodeError:
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _return_flow_state(context: WebhookProcessingContext) -> dict:
    rows = context.db.execute(
        select(Message)
        .where(
            Message.tenant_id == context.tenant_id,
            Message.phone == context.phone,
            Message.direction == "incoming",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(12)
    ).scalars().all()
    state: dict = {}
    for row in reversed(rows):
        text = str(row.message or "").strip()
        lowered = text.lower()
        if lowered.startswith("return_order:"):
            state["order_id"] = text.split(":", 1)[1].strip()
        elif lowered.startswith("return_item:"):
            parts = text.split(":")
            if len(parts) >= 3:
                state["order_id"] = parts[1].strip()
                state["item_ids"] = [parts[2].strip()]
        elif _looks_like_order_id(text) and _message_is_in_return_context(context, row.id):
            state["order_id"] = text.strip().lstrip("#")
        elif _is_return_reason(lowered):
            state["reason"] = _return_reason_label(lowered)
        elif _is_return_outcome(lowered):
            state["outcome"] = _return_outcome_label(lowered)
    return state


def _return_summary_text(context: WebhookProcessingContext, state: dict) -> str:
    order_id = state.get("order_id") or "the selected order"
    reason = state.get("reason") or "not specified"
    outcome = state.get("outcome") or "return request"
    item_text = ""
    item_ids = state.get("item_ids") if isinstance(state.get("item_ids"), list) else []
    if item_ids:
        order = find_order_for_customer(context.db, context.phone, order_id, tenant_id=context.tenant_id)
        items = _order_items(order)
        try:
            item = items[int(item_ids[0])]
            item_text = f"\nItem: {item.get('name') or item.get('title') or item.get('sku') or f'Item {int(item_ids[0]) + 1}'}"
        except (IndexError, TypeError, ValueError):
            item_text = f"\nItem: #{item_ids[0]}"
    return f"Order: {order_id}{item_text}\nReason: {reason}\nPreference: {outcome}"


def _return_reason_label(text: str) -> str:
    labels = {
        "return:damaged": "Damaged or defective",
        "damaged": "Damaged or defective",
        "return:wrong": "Wrong product received",
        "wrong product": "Wrong product received",
        "return:style": "Color, size, or feel issue",
        "doesn't suit": "Color, size, or feel issue",
        "return:changed": "Changed mind",
        "changed mind": "Changed mind",
    }
    return labels.get(text, text)


def _return_outcome_label(text: str) -> str:
    labels = {
        "return:refund": "Refund",
        "refund": "Refund",
        "return:exchange": "Exchange",
        "exchange": "Exchange",
        "return:credit": "Store credit",
        "store credit": "Store credit",
    }
    return labels.get(text, text)


def _brand_payload(context: WebhookProcessingContext) -> dict:
    try:
        row = get_tenant_config(context.db, context.tenant_id)
    except Exception:
        row = None
    return serialize_tenant_config(row) if row else {"tenant_id": context.tenant_id, "brand_name": "our store"}


def _commerce_flow_settings(context: WebhookProcessingContext) -> dict:
    metadata = _brand_payload(context).get("metadata") or {}
    flow_settings = metadata.get("flow_settings") if isinstance(metadata, dict) else {}
    commerce = flow_settings.get("commerce") if isinstance(flow_settings, dict) else {}
    return commerce if isinstance(commerce, dict) else {}


def _flow_text(context: WebhookProcessingContext, key: str, fallback: str, **values) -> str:
    raw = str(_commerce_flow_settings(context).get(key) or "").strip()
    template = raw or fallback
    format_values = defaultdict(str, {"brand_name": _brand_name(context), **values})
    try:
        return template.format_map(format_values)
    except (KeyError, ValueError):
        return fallback.format_map(format_values)


def _flow_buttons(context: WebhookProcessingContext, key: str, fallback: list[dict], **values) -> list[dict]:
    raw = _commerce_flow_settings(context).get(key)
    buttons = raw if isinstance(raw, list) else fallback
    clean_buttons = []
    format_values = defaultdict(str, {"brand_name": _brand_name(context), **values})
    for index, button in enumerate(buttons):
        if not isinstance(button, dict):
            continue
        button_id = str(button.get("id") or fallback[min(index, len(fallback) - 1)].get("id") or "").strip()
        title = str(button.get("title") or "").strip()[:20]
        try:
            button_id = button_id.format_map(format_values)
            title = title.format_map(format_values)[:20]
        except (KeyError, ValueError):
            pass
        if button_id and title:
            clean_buttons.append({"id": button_id, "title": title})
    return clean_buttons[:3] or fallback[:3]


def _brand_name(context: WebhookProcessingContext) -> str:
    return str(_brand_payload(context).get("brand_name") or "our store").strip() or "our store"


def _first_time_offer_text(context: WebhookProcessingContext) -> str:
    brand_name = _brand_name(context)
    discounts = _brand_payload(context).get("discount_rules") or []
    code = next((str(rule.get("code") or "").strip() for rule in discounts if rule.get("code")), "")
    if code:
        return _flow_text(
            context,
            "first_time_offer_with_code",
            "First time at {brand_name}. Welcome.\n\nUse code {code} on your first order.",
            brand_name=brand_name,
            code=code,
        )
    return _flow_text(
        context,
        "first_time_offer_no_code",
        "First time at {brand_name}. Welcome.\n\nWant to see best sellers or browse the catalog?",
        brand_name=brand_name,
    )


async def _send_text(context: WebhookProcessingContext, text: str) -> None:
    from app.modules.whatsapp.webhooks.processing.replies import _send_text_reply

    await _send_text_reply(context, text)


__all__ = ["_handle_commerce_interactive_flows", "send_checkout_cta", "send_bundle_push"]
