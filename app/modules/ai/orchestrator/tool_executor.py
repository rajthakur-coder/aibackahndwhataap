import json
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import AgentAction, HandoffTicket, Lead
from app.models.ecommerce import EcommerceCart, EcommerceConnection, EcommerceProduct, EcommerceReturnRequest
from app.modules.ai.orchestrator.confirmation_service import (
    cancel_confirmation,
    confirmation_from_message,
    consume_confirmation,
    create_confirmation,
    needs_confirmation,
)
from app.modules.ai.orchestrator.response_schema import ToolCallResult
from app.modules.ai.orchestrator.tool_registry import is_core_tool, normalize_tool_name, requires_confirmation
from app.modules.ai.recommendations.sales_recommendations_service import (
    find_cross_sell_products,
    find_product_recommendations,
    find_top_selling_products,
)
from app.modules.ai.search.product_search_service import product_search_text, score_search_text, search_terms
from app.modules.ecommerce.catalog.product_service import product_knowledge_text
from app.modules.ecommerce.orders.order_service import find_order_for_customer
from app.modules.ecommerce.bundles.bundle_service import manual_bundle_products
from app.modules.ecommerce.providers.shopify.product_api import fetch_fulfillments
from app.modules.ecommerce.shipping import fetch_courier_tracking
from app.modules.integrations.notification_service import notify_bulk_lead, notify_support_ticket
from app.modules.headless.custom_tool_service import execute_custom_tool, get_custom_tool
from app.modules.headless.oms_adapter import oms_adapter_registry
from app.modules.knowledge.knowledge_service import knowledge_context
from app.modules.automation.events.order_event_service import create_abandoned_cart_event
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


ORDER_RE = re.compile(
    r"\b(?:order|ord|booking|invoice)(?:\s*(?:id|number|no))?\s*(?:#|:|-)\s*([A-Za-z0-9][A-Za-z0-9-]{1,})\b"
    r"|\b(?:order|ord|booking|invoice)\s+(?:id|number|no)\s+([A-Za-z0-9][A-Za-z0-9-]{1,})\b"
    r"|#([A-Za-z0-9][A-Za-z0-9-]{1,})\b",
    re.I,
)
BARE_ORDER_RE = re.compile(r"^\s*#?([A-Za-z0-9][A-Za-z0-9-]{2,})\s*$")


def execute_tool(
    db: Session,
    tool_name: str,
    *,
    phone: str,
    message: str,
    entities: dict | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> ToolCallResult:
    requested_tool = str(tool_name or "").strip()
    entities = entities or {}
    tenant_id = normalize_tenant_id(tenant_id)
    confirmation_id, confirmed = confirmation_from_message(message)
    if confirmation_id and confirmed is False:
        cancel_confirmation(db, confirmation_id=confirmation_id, phone=phone, tenant_id=tenant_id)
        return ToolCallResult("confirmation", "cancelled", "Okay, I have not made that change.", {"confirmation_id": confirmation_id})
    if confirmation_id and confirmed is True:
        payload = consume_confirmation(db, confirmation_id=confirmation_id, phone=phone, tenant_id=tenant_id)
        if not payload:
            return ToolCallResult("confirmation", "not_found", "That confirmation has expired or was already used.", {"confirmation_id": confirmation_id})
        requested_tool = str(payload.get("tool_name") or requested_tool)
        entities = {**(payload.get("entities") or {}), **entities, "confirmed": True, "confirmation_id": confirmation_id}
        message = str(payload.get("message") or message)

    if not is_core_tool(requested_tool):
        return _execute_custom_tool(db, requested_tool, phone=phone, message=message, entities=entities, tenant_id=tenant_id)

    tool_name = normalize_tool_name(requested_tool)
    if requires_confirmation(tool_name) and needs_confirmation(tool_name, entities):
        confirmation = create_confirmation(
            db,
            tenant_id=tenant_id,
            phone=phone,
            tool_name=tool_name,
            message=message,
            entities=entities,
        )
        return ToolCallResult(
            tool_name,
            "needs_confirmation",
            "Please confirm before I continue.",
            confirmation,
        )
    handlers = {
        "get_order_status": _get_order_status,
        "get_dispatch_details": _get_dispatch_details,
        "get_tracking_link": _get_tracking_link,
        "search_catalog": _search_catalog,
        "get_product": _get_product,
        "get_policy": _get_policy,
        "get_bundle_recommendations": _get_bundle_recommendations,
        "create_support_ticket": _create_support_ticket,
        "add_to_cart": _add_to_cart,
        "generate_checkout_link": _generate_checkout_link,
        "apply_discount": _apply_discount,
        "get_return_eligibility": _get_return_eligibility,
        "initiate_return": _initiate_return,
        "log_bulk_lead": _log_bulk_lead,
    }
    return handlers[tool_name](db, phone=phone, message=message, entities=entities, tenant_id=tenant_id)


def _execute_custom_tool(
    db: Session,
    tool_name: str,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    if not get_custom_tool(db, tenant_id, tool_name):
        return ToolCallResult("get_policy", "not_found", "I do not have a tool for that request yet.", {"tool": tool_name})
    try:
        result = execute_custom_tool(db, tenant_id, tool_name, phone=phone, message=message, entities=entities)
    except Exception as exc:
        return ToolCallResult(tool_name, "failed", "That custom tool failed, so I could not complete the action.", {"error": str(exc)})
    status = str(result.get("status") or "success")
    message_text = str(result.get("message") or f"Custom tool {tool_name} completed.")
    return ToolCallResult(tool_name, status, message_text, result)


def _get_order_status(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    order_id = entities.get("order_id") or _extract_order_id(message)
    order = find_order_for_customer(db, phone, order_id, tenant_id=tenant_id)
    if not order:
        prompt = "Please share your order ID or the phone number used for the order."
        if order_id:
            prompt = f"I could not find order {order_id}. Please check the order ID or share your phone number."
        return ToolCallResult("get_order_status", "not_found", prompt, {"order_id": order_id})

    data = {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "shipment_status": order.shipment_status,
        "delivery_status": order.delivery_status,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "courier_company": order.courier_company,
        "total": order.total,
        "currency": order.currency,
        "items": _json_loads(order.items, []),
        "updated_at": str(order.updated_at) if order.updated_at else None,
    }
    return ToolCallResult(
        "get_order_status",
        "success",
        f"Found order {order.order_number}.",
        data,
    )


def _get_dispatch_details(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    order_id = entities.get("order_id") or _extract_order_id(message)
    order = find_order_for_customer(db, phone, order_id, tenant_id=tenant_id)
    if not order:
        return ToolCallResult("get_dispatch_details", "needs_input", "Please share the order ID for dispatch details.", {"order_id": order_id})
    fulfillments = _live_fulfillments(db, order, tenant_id)
    courier = _live_courier_tracking(order, fulfillments)
    data = {
        "order_id": order.external_id,
        "order_number": order.order_number,
        "fulfillment_status": order.fulfillment_status,
        "shipment_status": order.shipment_status,
        "delivery_status": order.delivery_status,
        "courier_company": courier.get("courier_company") or order.courier_company,
        "tracking_number": courier.get("tracking_number") or order.tracking_number,
        "tracking_url": courier.get("tracking_url") or order.tracking_url,
        "eta": courier.get("eta"),
        "current_location": courier.get("current_location"),
        "courier_status": courier.get("status"),
        "fulfillments": fulfillments,
    }
    return ToolCallResult("get_dispatch_details", "success", f"Dispatch details found for {order.order_number}.", data)


def _get_tracking_link(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    details = _get_dispatch_details(db, phone=phone, message=message, entities=entities, tenant_id=tenant_id)
    if details.status != "success" or not isinstance(details.data, dict):
        return ToolCallResult("get_tracking_link", details.status, details.message, details.data)
    tracking_url = details.data.get("tracking_url") or _first_fulfillment_value(details.data.get("fulfillments") or [], "tracking_url")
    tracking_number = details.data.get("tracking_number") or _first_fulfillment_value(details.data.get("fulfillments") or [], "tracking_number")
    if not tracking_url and not tracking_number:
        return ToolCallResult("get_tracking_link", "not_found", "Tracking is not available for this order yet.", details.data)
    return ToolCallResult(
        "get_tracking_link",
        "success",
        "Tracking details found.",
        {"tracking_url": tracking_url, "tracking_number": tracking_number, "order_number": details.data.get("order_number")},
    )


def _search_catalog(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    limit = _limit(entities.get("limit"), default=5)
    query = _catalog_query(message, entities)
    if _is_top_selling_query(query):
        products = find_top_selling_products(db, limit=limit, tenant_id=tenant_id)
    else:
        products = find_product_recommendations(db, query, limit=limit, tenant_id=tenant_id)
    if not products:
        products = _db_product_search(db, query, tenant_id, limit)
    if not products:
        return ToolCallResult("search_catalog", "not_found", "I could not find matching products yet.", [])
    return ToolCallResult("search_catalog", "success", f"Found {len(products)} matching products.", products)


def _get_product(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    query = str(entities.get("sku") or entities.get("query") or message or "").strip()
    product = _find_product(db, query, tenant_id)
    if not product:
        return ToolCallResult("get_product", "not_found", "I could not find that product in the catalog.", {"query": query})
    return ToolCallResult(
        "get_product",
        "success",
        f"Found product {product.title}.",
        {
            "product": _product_dict(product),
            "knowledge": product_knowledge_text(product),
        },
    )


def _get_policy(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    topic = str(entities.get("topic") or entities.get("policy_type") or message or "").strip()
    context = knowledge_context(db, topic or message, tenant_id=tenant_id)
    if not context:
        return ToolCallResult(
            "get_policy",
            "not_found",
            "I do not have that policy information yet.",
            {"topic": topic},
        )
    return ToolCallResult("get_policy", "success", "Found matching policy or FAQ context.", {"context": context})


def _get_bundle_recommendations(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    base_product = _find_product(db, str(entities.get("sku") or entities.get("query") or message), tenant_id)
    manual = manual_bundle_products(db, getattr(base_product, "sku", None) or entities.get("sku"), tenant_id)
    if manual and manual.get("products"):
        return ToolCallResult(
            "get_bundle_recommendations",
            "success",
            f"Found {len(manual['products'])} manually paired products.",
            {"base_products": [_product_dict(base_product)] if base_product else [], "recommendations": manual["products"], "pairing": manual["pairing"]},
        )
    base_products = [_product_dict(base_product)] if base_product else find_product_recommendations(db, message, limit=1, tenant_id=tenant_id)
    if not base_products:
        return ToolCallResult("get_bundle_recommendations", "needs_input", "Which product should I pair this with?", [])
    products = find_cross_sell_products(db, message, base_products, limit=3, tenant_id=tenant_id)
    if not products:
        return ToolCallResult("get_bundle_recommendations", "not_found", "I could not find bundle recommendations yet.", [])
    return ToolCallResult(
        "get_bundle_recommendations",
        "success",
        f"Found {len(products)} complementary products.",
        {"base_products": base_products, "recommendations": products},
    )


def _create_support_ticket(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    issue = str(entities.get("issue") or message or "Customer needs support").strip()
    history = str(entities.get("conversation_history") or "").strip()
    email = str(entities.get("email") or "").strip() or None
    summary = "\n".join(part for part in [issue, history, f"email: {email}" if email else ""] if part)[-5000:]
    ticket = HandoffTicket(tenant_id=tenant_id, phone=phone, reason="async_support", status="open", summary=summary)
    db.add(ticket)
    db.flush()
    db.add(
        AgentAction(
            tenant_id=tenant_id,
            phone=phone,
            action_type="support_ticket_created",
            status="open",
            payload=json.dumps({"issue": issue, "email": email}, ensure_ascii=True),
            result=json.dumps({"ticket_id": ticket.id}, ensure_ascii=True),
        )
    )
    db.commit()
    db.refresh(ticket)
    notify_support_ticket(
        db,
        tenant_id=tenant_id,
        phone=phone,
        issue=issue,
        summary=summary,
        ticket_id=ticket.id,
        email=email,
    )
    return ToolCallResult(
        "create_support_ticket",
        "success",
        f"I logged this as ticket #{ticket.id}. The team can follow up asynchronously.",
        {"ticket_id": ticket.id, "status": ticket.status, "email": email},
    )


def _add_to_cart(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    qty = _limit(entities.get("qty") or entities.get("quantity"), default=1)
    product = _find_product(db, str(entities.get("sku") or entities.get("query") or message), tenant_id)
    if not product:
        return ToolCallResult("add_to_cart", "needs_input", "Which product should I add to cart?", {"query": message})

    cart = _open_cart(db, phone, tenant_id)
    items = _json_loads(cart.items, [])
    product_payload = _product_dict(product)
    item_key = product_payload.get("sku") or product_payload.get("external_id") or product_payload.get("title")
    existing = next((item for item in items if item.get("key") == item_key), None)
    if existing:
        existing["qty"] = int(existing.get("qty") or 0) + qty
    else:
        items.append(
            {
                "key": item_key,
                "sku": product_payload.get("sku"),
                "external_id": product_payload.get("external_id"),
                "title": product_payload.get("title"),
                "qty": qty,
                "price": product_payload.get("price_min") or product_payload.get("price_max"),
                "product_url": product_payload.get("product_url"),
            }
        )
    cart.items = _json_dumps(items)
    cart.currency = product_payload.get("currency") or cart.currency
    cart.status = "open"
    db.commit()
    db.refresh(cart)
    _queue_abandoned_cart_recovery(db, cart, tenant_id)
    return ToolCallResult(
        "add_to_cart",
        "success",
        f"Added {product.title} to cart.",
        {"cart_id": cart.id, "items": items, "checkout_ready": bool(cart.checkout_url)},
    )


def _generate_checkout_link(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    cart = _cart_by_id(db, entities.get("cart_id"), tenant_id) if entities.get("cart_id") else _open_cart(db, phone, tenant_id)
    items = _json_loads(cart.items, []) if cart else []
    if not cart or not items:
        return ToolCallResult("generate_checkout_link", "needs_input", "Your cart is empty. Which product should I add?", {})

    checkout_url = str(entities.get("checkout_url") or cart.checkout_url or "").strip()
    if checkout_url:
        cart.checkout_url = checkout_url
        cart.status = "checkout_ready"
        db.commit()
        return ToolCallResult(
            "generate_checkout_link",
            "success",
            "Checkout link is ready.",
            {"cart_id": cart.id, "checkout_url": checkout_url, "items": items},
        )

    connection = _active_oms_connection(db, tenant_id)
    if not connection:
        cart.status = "checkout_pending"
        db.commit()
        return ToolCallResult(
            "generate_checkout_link",
            "needs_integration",
            "Cart is saved. Connect an OMS to generate a real checkout link.",
            {"cart_id": cart.id, "items": items},
        )

    discount = _json_loads(cart.metadata_json, {}).get("discount")
    adapter = oms_adapter_registry.for_connection(connection)
    if not adapter:
        cart.status = "checkout_pending"
        db.commit()
        return ToolCallResult("generate_checkout_link", "needs_integration", "Cart is saved. This OMS adapter is not available yet.", {"cart_id": cart.id, "items": items, "platform": connection.platform})

    draft_order = adapter.create_draft_order(
        items,
        {
            "phone": phone,
            "email": entities.get("email"),
            "discount": discount,
            "note": f"WhatsApp cart #{cart.id}",
        },
    )
    checkout_url = draft_order.get("invoice_url") or draft_order.get("checkout_url") or draft_order.get("payment_url") or draft_order.get("admin_graphql_api_id") or ""
    cart.checkout_url = checkout_url
    cart.status = "checkout_ready" if checkout_url else "checkout_pending"
    cart.metadata_json = _json_dumps({**_json_loads(cart.metadata_json, {}), "oms_draft_order": draft_order, "oms_platform": connection.platform})
    db.commit()
    return ToolCallResult(
        "generate_checkout_link",
        "success" if checkout_url else "needs_integration",
        "Checkout link is ready." if checkout_url else "Draft order created, but the OMS did not return a checkout URL.",
        {"cart_id": cart.id, "checkout_url": checkout_url, "items": items, "draft_order_id": draft_order.get("id")},
    )


def _apply_discount(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    code = str(entities.get("code") or _extract_discount_code(message) or "").strip().upper()
    cart = _cart_by_id(db, entities.get("cart_id"), tenant_id) if entities.get("cart_id") else _open_cart(db, phone, tenant_id)
    if not code:
        return ToolCallResult("apply_discount", "needs_input", "Please share the discount code.", {"cart_id": cart.id})
    rule = _discount_rule(db, tenant_id, code)
    if not rule:
        return ToolCallResult("apply_discount", "not_found", f"Discount code {code} is not configured.", {"code": code, "cart_id": cart.id})
    metadata = _json_loads(cart.metadata_json, {})
    metadata["discount"] = _discount_for_oms(rule)
    metadata["discount_rule"] = rule
    cart.metadata_json = _json_dumps(metadata)
    db.commit()
    return ToolCallResult("apply_discount", "success", f"Applied discount {code}.", {"cart_id": cart.id, "discount": rule})


def _get_return_eligibility(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    order_id = entities.get("order_id") or _extract_order_id(message)
    order = find_order_for_customer(db, phone, order_id, tenant_id=tenant_id)
    if not order:
        return ToolCallResult("get_return_eligibility", "needs_input", "Please share the order ID for the return.", {"order_id": order_id})

    window_days = _return_window_days(db, tenant_id)
    delivered_at = _order_delivered_at(order)
    if not delivered_at:
        eligibility = {
            "eligible": False,
            "reason": "Delivery date is not available in cached order data.",
            "return_window_days": window_days,
        }
    else:
        days_since_delivery = (datetime.now(timezone.utc) - delivered_at).days
        eligibility = {
            "eligible": days_since_delivery <= window_days,
            "days_since_delivery": days_since_delivery,
            "return_window_days": window_days,
            "reason": "Within return window" if days_since_delivery <= window_days else "Outside return window",
        }
    eligibility.update({"order_id": order.external_id, "order_number": order.order_number})
    return ToolCallResult("get_return_eligibility", "success", "Return eligibility checked.", eligibility)


def _initiate_return(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    eligibility_result = _get_return_eligibility(db, phone=phone, message=message, entities=entities, tenant_id=tenant_id)
    eligibility = eligibility_result.data if isinstance(eligibility_result.data, dict) else {}
    if eligibility_result.status != "success" or not eligibility.get("eligible"):
        return ToolCallResult(
            "initiate_return",
            "not_eligible",
            "I cannot initiate this return from the available data.",
            eligibility,
        )

    reason = str(entities.get("reason") or message or "").strip()
    outcome = str(entities.get("outcome") or "return request").strip()
    item_ids = entities.get("item_ids") if isinstance(entities.get("item_ids"), list) else []
    request = EcommerceReturnRequest(
        tenant_id=tenant_id,
        phone=phone,
        order_id=str(eligibility.get("order_id") or ""),
        order_number=str(eligibility.get("order_number") or ""),
        status="requested",
        reason=reason,
        item_ids=_json_dumps(item_ids),
        eligibility=_json_dumps(eligibility),
        notes=f"Preference: {outcome}. Created by AI commerce action. OMS return adapter still needs final processing.",
    )
    db.add(request)
    db.flush()
    _mark_oms_return_requested(db, tenant_id, eligibility, request)
    db.commit()
    db.refresh(request)
    return ToolCallResult(
        "initiate_return",
        "success",
        f"Return request #{request.id} has been logged.",
        {"return_request_id": request.id, "status": request.status, "outcome": outcome, "eligibility": eligibility},
    )


def _log_bulk_lead(
    db: Session,
    *,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    name = str(entities.get("name") or "").strip() or None
    email = str(entities.get("email") or "").strip() or None
    occasion = str(entities.get("occasion") or entities.get("use_case") or message or "").strip()
    qty = str(entities.get("qty") or entities.get("quantity") or "").strip()
    timeline = str(entities.get("timeline") or "").strip()
    notes = {
        "tenant_id": tenant_id,
        "occasion": occasion,
        "qty": qty,
        "timeline": timeline,
        "budget": entities.get("budget"),
        "city": entities.get("city"),
        "source_message": message,
    }
    lead = Lead(
        tenant_id=tenant_id,
        phone=phone,
        name=name,
        email=email,
        intent="bulk_gifting",
        status="new",
        source="whatsapp",
        notes=_json_dumps(notes),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    notify_bulk_lead(
        db,
        tenant_id=tenant_id,
        phone=phone,
        lead_id=lead.id,
        payload=notes,
        email=email,
    )
    return ToolCallResult(
        "log_bulk_lead",
        "success",
        f"Bulk/gifting lead #{lead.id} has been logged.",
        {"lead_id": lead.id, "status": lead.status, "email": email, "occasion": occasion, "qty": qty, "timeline": timeline},
    )


def _db_product_search(db: Session, query: str, tenant_id: str, limit: int) -> list[dict]:
    query_terms = search_terms(query)
    rows = db.execute(
        select(EcommerceProduct)
        .where(EcommerceProduct.tenant_id == tenant_id)
        .order_by(EcommerceProduct.updated_at.desc())
        .limit(500)
    ).scalars().all()
    scored = [
        (score_search_text(query_terms, product_search_text(product)), product)
        for product in rows
    ]
    ranked = [product for score, product in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0]
    if not ranked and rows:
        ranked = rows[:limit]
    return [_product_dict(product) for product in ranked[:limit]]


def _find_product(db: Session, query: str, tenant_id: str) -> EcommerceProduct | None:
    query = (query or "").strip()
    if not query:
        return None
    exact = db.execute(
        select(EcommerceProduct)
        .where(
            EcommerceProduct.tenant_id == tenant_id,
            (
                (EcommerceProduct.sku == query)
                | (EcommerceProduct.external_id == query)
                | (EcommerceProduct.shopify_product_id == query)
                | (EcommerceProduct.title == query)
            ),
        )
        .limit(1)
    ).scalars().first()
    if exact:
        return exact
    results = _db_product_search(db, query, tenant_id, 1)
    if not results:
        return None
    external_id = str(results[0].get("external_id") or "")
    return db.execute(
        select(EcommerceProduct)
        .where(EcommerceProduct.tenant_id == tenant_id, EcommerceProduct.external_id == external_id)
        .limit(1)
    ).scalars().first()


def _product_dict(product: EcommerceProduct) -> dict:
    image_urls = _json_loads(product.image_urls, [])
    variants = _json_loads(product.variants, [])
    first_variant = variants[0] if variants and isinstance(variants[0], dict) else {}
    return {
        "id": product.id,
        "title": product.title,
        "sku": product.sku,
        "external_id": product.external_id,
        "shopify_product_id": product.shopify_product_id,
        "product_url": product.product_url,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "currency": product.currency,
        "description": product.description,
        "product_type": product.product_type,
        "tags": product.tags,
        "status": product.status,
        "inventory": product.inventory,
        "image_url": image_urls[0] if image_urls else None,
        "image_urls": image_urls,
        "variant_id": str(first_variant.get("id") or "") if first_variant else None,
    }


def _extract_order_id(message: str) -> str | None:
    match = ORDER_RE.search(message or "")
    if match:
        return next((group.upper() for group in match.groups() if group), None)
    bare_match = BARE_ORDER_RE.match(message or "")
    return bare_match.group(1).upper() if bare_match else None


def _catalog_query(message: str, entities: dict) -> str:
    values = [
        message,
        entities.get("category"),
        entities.get("product_type"),
        entities.get("color"),
        entities.get("size"),
        entities.get("material"),
        entities.get("use_case"),
    ]
    attributes = entities.get("attributes") if isinstance(entities.get("attributes"), list) else []
    values.extend(attributes)
    return " ".join(str(value or "") for value in values).strip()


def _is_top_selling_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(phrase in lowered for phrase in ("top selling", "best selling", "most sold", "highest sale"))


def _extract_discount_code(message: str) -> str | None:
    match = re.search(r"\b([A-Z][A-Z0-9_]{3,20})\b", message or "", flags=re.I)
    return match.group(1) if match else None


def _limit(value, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 10))


def _open_cart(db: Session, phone: str, tenant_id: str) -> EcommerceCart:
    cart = db.execute(
        select(EcommerceCart)
        .where(EcommerceCart.tenant_id == tenant_id, EcommerceCart.phone == phone, EcommerceCart.status.in_(("open", "checkout_pending")))
        .order_by(EcommerceCart.updated_at.desc())
        .limit(1)
    ).scalars().first()
    if cart:
        return cart
    cart = EcommerceCart(tenant_id=tenant_id, phone=phone, status="open", items="[]")
    db.add(cart)
    db.flush()
    return cart


def _cart_by_id(db: Session, cart_id, tenant_id: str) -> EcommerceCart | None:
    try:
        parsed = int(cart_id)
    except (TypeError, ValueError):
        return None
    return db.execute(
        select(EcommerceCart).where(EcommerceCart.tenant_id == tenant_id, EcommerceCart.id == parsed).limit(1)
    ).scalars().first()


def _active_oms_connection(db: Session, tenant_id: str) -> EcommerceConnection | None:
    return db.execute(
        select(EcommerceConnection)
        .where(
            EcommerceConnection.tenant_id == tenant_id,
            EcommerceConnection.platform.in_(tuple(oms_adapter_registry.list_platforms())),
            EcommerceConnection.status == "active",
        )
        .order_by(EcommerceConnection.updated_at.desc())
        .limit(1)
    ).scalars().first()


def _discount_rule(db: Session, tenant_id: str, code: str) -> dict | None:
    from app.modules.tenants.tenant_service import get_tenant_config

    config = get_tenant_config(db, tenant_id)
    rules = _json_loads(getattr(config, "discount_rules", None), [])
    for rule in rules:
        if str(rule.get("code") or "").upper() == code:
            return rule
    return None


def _discount_for_oms(rule: dict) -> dict:
    rule_type = str(rule.get("type") or "").lower()
    if rule_type == "percentage":
        return {"code": rule.get("code"), "type": "percentage", "value_type": "percentage", "value": rule.get("value")}
    if rule_type in {"fixed", "fixed_amount"}:
        return {"code": rule.get("code"), "type": "fixed_amount", "value_type": "fixed_amount", "value": rule.get("value")}
    return {"code": rule.get("code"), "type": rule_type, "value": rule.get("value")}


def _queue_abandoned_cart_recovery(db: Session, cart: EcommerceCart, tenant_id: str) -> None:
    try:
        create_abandoned_cart_event(
            db,
            payload={
                "external_id": f"whatsapp-cart:{cart.id}",
                "cart_id": cart.id,
                "phone": cart.phone,
                "cart_url": cart.checkout_url or "",
                "currency": cart.currency or "",
                "items": _json_loads(cart.items, []),
            },
            source="whatsapp_ai_cart",
        )
    except Exception as exc:
        db.add(
            AgentAction(
                tenant_id=tenant_id,
                phone=cart.phone,
                action_type="abandoned_cart_hook_failed",
                status="failed",
                payload=_json_dumps({"cart_id": cart.id, "tenant_id": tenant_id}),
                result=_json_dumps({"error": str(exc)}),
            )
        )
        db.commit()


def _live_fulfillments(db: Session, order, tenant_id: str) -> list[dict]:
    connection = _active_oms_connection(db, tenant_id)
    if not connection or connection.platform != "shopify" or connection.id != order.connection_id or not order.external_id:
        return _json_loads(getattr(order, "raw_payload", None), {}).get("fulfillments") or []
    try:
        return fetch_fulfillments(connection, str(order.external_id))
    except Exception:
        return _json_loads(getattr(order, "raw_payload", None), {}).get("fulfillments") or []


def _live_courier_tracking(order, fulfillments: list[dict]) -> dict:
    tracking_number = order.tracking_number or _first_fulfillment_value(fulfillments, "tracking_number")
    courier = fetch_courier_tracking(awb=tracking_number, order_id=order.external_id)
    return courier or {}


def _return_confirmed(message: str, entities: dict) -> bool:
    if entities.get("confirmed") is True:
        return True
    lowered = (message or "").lower()
    return "confirm:return:yes" in lowered or "yes, process return" in lowered


def _first_fulfillment_value(fulfillments: list[dict], key: str) -> str | None:
    for fulfillment in fulfillments:
        value = fulfillment.get(key)
        if value:
            return str(value)
        values = fulfillment.get(f"{key}s")
        if isinstance(values, list) and values:
            return str(values[0])
    return None


def _mark_oms_return_requested(db: Session, tenant_id: str, eligibility: dict, request: EcommerceReturnRequest) -> None:
    connection = _active_oms_connection(db, tenant_id)
    order_id = str(eligibility.get("order_id") or "")
    if not connection or not order_id:
        return
    adapter = oms_adapter_registry.for_connection(connection)
    if not adapter:
        return
    try:
        adapter.initiate_return(order_id, _json_loads(request.item_ids, []), request.reason)
    except Exception as exc:
        request.notes = "\n".join(filter(None, [request.notes, f"OMS return note update failed: {exc}"]))[-3000:]


def _return_window_days(db: Session, tenant_id: str) -> int:
    try:
        from app.modules.tenants.tenant_service import get_tenant_config

        config = get_tenant_config(db, tenant_id)
        policy = str(getattr(config, "return_policy", "") or "")
    except Exception:
        policy = ""
    match = re.search(r"\b(\d{1,3})\s*[- ]?day", policy, flags=re.I)
    return int(match.group(1)) if match else 7


def _order_delivered_at(order) -> datetime | None:
    delivery_state = " ".join(str(value or "").lower() for value in (order.delivery_status, order.shipment_status, order.fulfillment_status, order.status))
    if "delivered" not in delivery_state:
        return None
    value = order.updated_at
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
