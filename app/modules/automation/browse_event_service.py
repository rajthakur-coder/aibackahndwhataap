from sqlalchemy.orm import Session

from app.modules.automation.events.event_processor_service import create_automation_event


def create_browse_no_buy_event(
    db: Session,
    *,
    phone: str,
    tenant_id: str,
    products: list[dict] | None = None,
    customer_name: str | None = None,
    source: str = "whatsapp_catalog_browse",
    delay_seconds: int = 259200,
) -> dict:
    payload = {
        "tenant_id": tenant_id,
        "phone": phone,
        "customer_name": customer_name or "there",
        "products": products or [],
    }
    product_key = "-".join(str(product.get("sku") or product.get("external_id") or product.get("title") or "") for product in (products or [])[:3])
    event = create_automation_event(
        db,
        trigger="browse_no_buy",
        source=source,
        external_id=f"{tenant_id}:{phone}:{product_key or 'browse'}",
        phone=phone,
        payload=payload,
        delay_seconds=delay_seconds,
    )
    return {"status": "queued", "event_id": event.id, "scheduled_for": str(event.scheduled_for)}
