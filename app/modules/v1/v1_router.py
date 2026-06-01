from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.crm import HandoffTicket, Lead
from app.models.whatsapp import Message
from app.modules.analytics.analytics_service import commerce_dashboard
from app.modules.automation.broadcast_service import create_broadcast_campaign, list_broadcast_campaigns
from app.modules.automation.outbound_template_service import submit_outbound_templates_to_meta
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingRequest
from app.modules.ecommerce.bundles.bundle_service import upsert_bundle_pairing
from app.modules.ecommerce.orders.order_service import find_order_for_customer
from app.modules.ai.orchestrator.orchestrator_service import orchestrate_message
from app.modules.ai.orchestrator.tool_executor import execute_tool
from app.modules.ai.search.product_search_service import product_search_text, score_search_text, search_terms
from app.models.ecommerce import EcommerceProduct
from app.modules.ecommerce.webhooks.webhook_handler_service import (
    handle_shopify_fulfillments_webhook,
    handle_shopify_orders_webhook,
    handle_shopify_products_webhook,
)
from app.modules.whatsapp.webhooks.routing.webhook_router import receive_webhook
from app.modules.onboarding.onboarding_router import onboarding_router as _onboarding_router
from app.security import get_current_user_token
from app.shared.tenant import strict_tenant_id


v1_router = APIRouter(prefix="/v1", tags=["v1"])
v1_tenant_router = APIRouter(tags=["v1"], dependencies=[Depends(get_current_user_token)])
internal_router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[Depends(get_current_user_token)])


@v1_router.post("/webhook/whatsapp")
async def v1_whatsapp_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    return await receive_webhook(request, db)


@v1_router.post("/webhook/shopify")
async def v1_shopify_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    topic = (
        request.headers.get("X-Shopify-Topic")
        or request.headers.get("x-shopify-topic")
        or request.query_params.get("topic")
        or ""
    ).lower()

    def sync_op(sync_db):
        request_id = request.headers.get("X-Request-Id") or request.headers.get("X-Shopify-Webhook-Id")
        if "orders" in topic:
            return handle_shopify_orders_webhook(sync_db, raw_body, request.headers, request_id=request_id)
        if "products" in topic:
            return handle_shopify_products_webhook(sync_db, raw_body, request.headers, request_id=request_id)
        if "fulfillments" in topic or "fulfillment" in topic:
            return handle_shopify_fulfillments_webhook(sync_db, raw_body, request.headers, request_id=request_id)
        return {"status": "accepted", "reason": "unsupported_topic", "topic": topic or None}

    return await db.run_sync(sync_op)


@v1_router.post("/webhook/courier")
async def v1_courier_webhook():
    return {"status": "accepted"}


@v1_tenant_router.get("/conversations")
async def v1_conversations(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Message.phone).where(Message.tenant_id == tenant_id).distinct())
    return [{"phone": phone} for phone in result.scalars().all()]


@v1_tenant_router.get("/analytics/dashboard")
async def v1_analytics_dashboard(days: int = 30, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: commerce_dashboard(sync_db, tenant_id=tenant_id, days=days))


@v1_tenant_router.post("/templates/submit")
async def v1_submit_templates(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: submit_outbound_templates_to_meta(sync_db, tenant_id))


@v1_tenant_router.get("/broadcasts")
async def v1_list_broadcasts(limit: int = 50, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: list_broadcast_campaigns(sync_db, tenant_id, limit=limit))


@v1_tenant_router.post("/broadcasts")
async def v1_broadcasts(payload: dict, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    try:
        return await db.run_sync(lambda sync_db: create_broadcast_campaign(sync_db, tenant_id, payload))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@v1_tenant_router.post("/cross-sell/rules")
async def v1_cross_sell_rules(data: BundlePairingRequest, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(lambda sync_db: upsert_bundle_pairing(sync_db, data, tenant_id))


@v1_tenant_router.get("/leads/bulk")
async def v1_bulk_leads(tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Lead)
        .where(Lead.tenant_id == tenant_id, Lead.intent == "bulk_gifting")
        .order_by(Lead.created_at.desc())
    )
    return [
        {
            "id": row.id,
            "phone": row.phone,
            "name": row.name,
            "email": row.email,
            "status": row.status,
            "source": row.source,
            "notes": row.notes,
            "created_at": str(row.created_at),
        }
        for row in result.scalars().all()
    ]


@v1_tenant_router.post("/tickets/resolve")
async def v1_resolve_ticket(payload: dict, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    ticket_id = payload.get("ticket_id")
    if not ticket_id:
        raise HTTPException(status_code=400, detail="ticket_id is required")
    ticket = await db.get(HandoffTicket, int(ticket_id))
    if not ticket or ticket.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket.status = "closed"
    if payload.get("note"):
        ticket.summary = "\n".join(filter(None, [ticket.summary, f"resolved: {str(payload.get('note')).strip()}"]))[-5000:]
    await db.commit()
    await db.refresh(ticket)
    return {"status": "resolved", "ticket_id": ticket.id}


v1_router.include_router(v1_tenant_router)
v1_router.include_router(_onboarding_router, prefix="")


@internal_router.post("/llm/respond")
async def internal_llm_respond(payload: dict, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(
        lambda sync_db: orchestrate_message(
            sync_db,
            phone=str(payload.get("phone") or ""),
            message=str(payload.get("message") or ""),
            tenant_id=tenant_id,
        ).__dict__
    )


@internal_router.post("/tool/execute")
async def internal_tool_execute(payload: dict, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    return await db.run_sync(
        lambda sync_db: execute_tool(
            sync_db,
            str(payload.get("tool_name") or payload.get("name") or ""),
            phone=str(payload.get("phone") or ""),
            message=str(payload.get("message") or ""),
            entities=payload.get("entities") if isinstance(payload.get("entities"), dict) else {},
            tenant_id=tenant_id,
        ).__dict__
    )


@internal_router.get("/catalog/search")
async def internal_catalog_search(query: str, limit: int = 5, tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    query_terms = search_terms(query)

    def sync_op(sync_db):
        rows = sync_db.execute(
            select(EcommerceProduct)
            .where(EcommerceProduct.tenant_id == tenant_id)
            .order_by(EcommerceProduct.updated_at.desc())
            .limit(500)
        ).scalars().all()
        scored = sorted(
            ((score_search_text(query_terms, product_search_text(row)), row) for row in rows),
            key=lambda item: item[0],
            reverse=True,
        )
        return [
            {"id": row.id, "sku": row.sku, "title": row.title, "price_min": row.price_min, "product_url": row.product_url}
            for score, row in scored[: max(1, min(limit, 20))]
            if score > 0
        ]

    return await db.run_sync(sync_op)


@internal_router.get("/oms/order")
async def internal_oms_order(order_id: str, phone: str = "", tenant_id: str = Depends(strict_tenant_id), db: AsyncSession = Depends(get_db)):
    def sync_op(sync_db):
        row = find_order_for_customer(sync_db, phone=phone, order_id=order_id, tenant_id=tenant_id)
        if not row:
            return None
        return {"id": row.id, "order_number": row.order_number, "status": row.status, "total": row.total, "phone": row.phone}

    result = await db.run_sync(sync_op)
    if not result:
        raise HTTPException(status_code=404, detail="Order not found")
    return result
