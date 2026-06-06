import json
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.crm import AgentAction
from app.models.ecommerce import (
    ContactStoreMapping,
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
)
from app.modules.headless.oms_adapter import oms_adapter_registry

DELIVERED_STATUSES = {"delivered"}


from app.modules.ecommerce.orders.order_followup_service import *
from app.modules.ecommerce.orders.order_normalizer_service import *







def upsert_customer(db: Session, connection: EcommerceConnection, customer: dict | None) -> EcommerceCustomer | None:
    if not isinstance(customer, dict) or not customer.get("id"):
        return None
    row = EcommerceCustomer(
        tenant_id=connection.tenant_id,
        connection_id=connection.id,
        platform=connection.platform,
        external_id=str(customer.get("id")),
        shopify_customer_id=str(customer.get("id")),
    )
    row.name = _shopify_customer_name(customer)
    row.phone = customer.get("phone") or customer.get("default_address", {}).get("phone") or row.phone
    row.email = customer.get("email") or row.email
    row.total_orders = int(customer.get("orders_count") or row.total_orders or 0)
    row.total_spend = str(customer.get("total_spent") or row.total_spend or "")
    row.tags = customer.get("tags")
    row.addresses = _json_dumps(customer.get("addresses") or [])
    row.last_order_at = customer.get("updated_at") or row.last_order_at
    row.marketing_consent = _json_dumps(customer.get("email_marketing_consent") or {})
    row.preferred_language = customer.get("locale") or row.preferred_language
    db.add(row)
    upsert_contact_store_mapping(db, connection, row.phone, source="customer")
    return row




def upsert_order(db: Session, connection: EcommerceConnection, order: dict) -> EcommerceOrder:
    normalized = _normalize_order(connection, order)
    customer = upsert_customer(db, connection, normalized.get("customer"))
    row = _existing_order_row(db, connection, normalized)
    if not row:
        row = EcommerceOrder(
            tenant_id=connection.tenant_id,
            connection_id=connection.id,
            platform=connection.platform,
            external_id=normalized["external_id"],
            order_number=normalized["order_number"],
        )
        db.add(row)
    row.tenant_id = connection.tenant_id
    row.connection_id = connection.id
    row.platform = connection.platform
    row.external_id = normalized["external_id"]
    row.shopify_order_id = normalized["shopify_order_id"]
    row.ecommerce_customer_id = customer.id if customer else row.ecommerce_customer_id
    row.order_number = normalized["order_number"]
    row.phone = normalized["phone"] or row.phone
    row.email = normalized["email"] or row.email
    row.customer_name = normalized["customer_name"] or row.customer_name
    row.tags = normalized["tags"]
    row.note = normalized["note"]
    row.shipping_address = _json_dumps(normalized["shipping_address"])
    row.billing_address = _json_dumps(normalized["billing_address"])
    row.status = normalized["status"]
    row.fulfillment_status = normalized["fulfillment_status"]
    row.financial_status = normalized["financial_status"]
    row.subtotal = normalized["subtotal"]
    row.total = normalized["total"]
    row.discounts = normalized["discounts"]
    row.tax = normalized["tax"]
    row.currency = normalized["currency"]
    row.payment_gateway = normalized["payment_gateway"]
    row.tracking_number = normalized["tracking_number"]
    row.tracking_url = normalized["tracking_url"]
    row.tracking_numbers = _json_dumps(normalized["tracking_numbers"])
    row.tracking_urls = _json_dumps(normalized["tracking_urls"])
    row.courier_company = normalized["courier_company"]
    row.shipment_status = normalized["shipment_status"]
    row.delivery_status = normalized["delivery_status"]
    row.skus = _json_dumps(normalized["skus"])
    row.product_ids = _json_dumps(normalized["product_ids"])
    row.items = _json_dumps(normalized["items"])
    row.raw_payload = _json_dumps(order)
    row.shopify_created_at = normalized["shopify_created_at"]
    row.shopify_updated_at = normalized["shopify_updated_at"]
    upsert_contact_store_mapping(db, connection, row.phone, source="order")
    return row


def _existing_order_row(db: Session, connection: EcommerceConnection, normalized: dict) -> EcommerceOrder | None:
    order_number = str(normalized.get("order_number") or "").strip()
    clean_order_number = order_number.lstrip("#")
    external_id = str(normalized.get("external_id") or "").strip()
    shopify_order_id = str(normalized.get("shopify_order_id") or "").strip()
    matches = []
    if external_id:
        matches.append(EcommerceOrder.external_id == external_id)
    if shopify_order_id:
        matches.append(EcommerceOrder.shopify_order_id == shopify_order_id)
    if order_number:
        matches.extend(
            [
                EcommerceOrder.order_number == order_number,
                EcommerceOrder.order_number == clean_order_number,
                EcommerceOrder.order_number == f"#{clean_order_number}",
            ]
        )
    if not matches:
        return None
    return db.execute(
        select(EcommerceOrder)
        .where(
            EcommerceOrder.tenant_id == connection.tenant_id,
            EcommerceOrder.connection_id == connection.id,
            EcommerceOrder.platform == connection.platform,
            or_(*matches),
        )
        .order_by(EcommerceOrder.updated_at.desc(), EcommerceOrder.id.desc())
        .limit(1)
    ).scalars().first()


def upsert_contact_store_mapping(
    db: Session,
    connection: EcommerceConnection,
    phone: str | None,
    source: str = "auto",
) -> ContactStoreMapping | None:
    normalized_phone = _digits(phone)
    if not normalized_phone:
        return None
    for pending in db.new:
        if (
            isinstance(pending, ContactStoreMapping)
            and pending.tenant_id == connection.tenant_id
            and pending.normalized_phone == normalized_phone
        ):
            mapping = pending
            break
    else:
        mapping = None
    if mapping is None:
        db.flush()
        mapping = db.execute(
            select(ContactStoreMapping).where(
                ContactStoreMapping.tenant_id == connection.tenant_id,
                ContactStoreMapping.normalized_phone == normalized_phone,
            )
        ).scalars().first()
    if not mapping:
        mapping = ContactStoreMapping(
            tenant_id=connection.tenant_id,
            phone=str(phone or normalized_phone),
            normalized_phone=normalized_phone,
        )
        db.add(mapping)
    mapping.connection_id = connection.id
    mapping.phone = str(phone or normalized_phone)
    mapping.source = source
    mapping.status = "active"
    mapping.last_seen_at = datetime.utcnow()
    return mapping


def sync_orders(db: Session, connection: EcommerceConnection, limit: int = 50) -> dict:
    return {
        "status": "skipped",
        "reason": "live_api_mode",
        "message": "Orders are read directly from the ecommerce API and cached in Redis; they are not stored in Neon.",
        "connection_id": connection.id,
    }

def find_order_for_customer(
    db: Session,
    phone: str,
    order_id: str | None = None,
    tenant_id: str | None = None,
) -> EcommerceOrder | None:
    tenant_id = tenant_id or _tenant_for_phone(db, phone)
    if order_id:
        live_order = _find_live_order_for_customer(db, phone, order_id, tenant_id)
        if live_order:
            return live_order

    statement = select(EcommerceOrder)
    if order_id:
        normalized_order_id = order_id.strip().lstrip("#")
        statement = statement.where(
            (EcommerceOrder.order_number == order_id)
            | (EcommerceOrder.order_number == f"#{normalized_order_id}")
            | (EcommerceOrder.external_id == normalized_order_id)
        )
    else:
        statement = statement.where(EcommerceOrder.phone == phone)

    if tenant_id:
        statement = statement.where(EcommerceOrder.tenant_id == tenant_id)

    cached = db.execute(statement.order_by(EcommerceOrder.updated_at.desc())).scalars().first()
    if not order_id:
        live_order = _find_live_order_for_customer(db, phone, order_id, tenant_id)
        if live_order:
            return live_order
    if cached:
        return cached
    if order_id:
        return None
    return _find_live_order_for_customer(db, phone, order_id, tenant_id)


def list_recent_orders_for_customer(
    db: Session,
    phone: str,
    limit: int = 5,
    tenant_id: str | None = None,
) -> list[EcommerceOrder]:
    tenant_id = tenant_id or _tenant_for_phone(db, phone)
    statement = select(EcommerceOrder).where(EcommerceOrder.phone == phone)
    if tenant_id:
        statement = statement.where(EcommerceOrder.tenant_id == tenant_id)
    rows = db.execute(statement.order_by(EcommerceOrder.updated_at.desc()).limit(max(1, min(limit, 10)))).scalars().all()
    if rows:
        return rows

    _find_live_order_for_customer(db, phone, None, tenant_id)
    statement = select(EcommerceOrder).where(EcommerceOrder.phone == phone)
    if tenant_id:
        statement = statement.where(EcommerceOrder.tenant_id == tenant_id)
    return db.execute(statement.order_by(EcommerceOrder.updated_at.desc()).limit(max(1, min(limit, 10)))).scalars().all()


def _find_live_order_for_customer(
    db: Session,
    phone: str,
    order_id: str | None = None,
    tenant_id: str | None = None,
) -> EcommerceOrder | None:
    connections = _active_order_connections(db, tenant_id)
    for connection in connections:
        try:
            payload = _fetch_live_order_payload(connection, phone, order_id)
        except Exception as exc:
            db.add(
                AgentAction(
                    phone=phone,
                    action_type="live_order_lookup_failed",
                    status="failed",
                    payload=json.dumps({"connection_id": connection.id, "order_id": order_id}),
                    result=json.dumps({"error": str(exc)}),
                )
            )
            db.commit()
            continue
        if not payload:
            continue
        order = upsert_order(db, connection, payload)
        db.commit()
        db.refresh(order)
        return order
    return None


def _fetch_live_order_payload(connection: EcommerceConnection, phone: str, order_id: str | None) -> dict | None:
    adapter = oms_adapter_registry.for_connection(connection)
    if not adapter:
        return None

    if order_id:
        clean_order_id = str(order_id or "").strip().lstrip("#")
        return adapter.get_order(clean_order_id)

    orders = adapter.list_orders(phone)
    return orders[0] if orders else None


def _active_order_connections(db: Session, tenant_id: str | None = None) -> list[EcommerceConnection]:
    statement = select(EcommerceConnection).where(
        EcommerceConnection.status == "active",
        EcommerceConnection.platform.in_(tuple(oms_adapter_registry.list_platforms())),
    )
    if tenant_id:
        statement = statement.where(EcommerceConnection.tenant_id == tenant_id)
    return db.execute(statement.order_by(EcommerceConnection.updated_at.desc())).scalars().all()


def _tenant_for_phone(db: Session, phone: str) -> str | None:
    from app.models.ecommerce import ContactStoreMapping

    normalized_phone = _digits(phone)
    if not normalized_phone:
        return None
    try:
        mapping = db.execute(
            select(ContactStoreMapping)
            .where(ContactStoreMapping.normalized_phone == normalized_phone)
            .order_by(ContactStoreMapping.last_seen_at.desc())
            .limit(1)
        ).scalars().first()
    except Exception:
        db.rollback()
        return None
    return mapping.tenant_id if mapping else None






