import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import (
    ContactStoreMapping,
    EcommerceConnection,
    EcommerceCustomer,
    EcommerceOrder,
)


def active_shopify_connection(db: Session, phone: str | None = None) -> EcommerceConnection | None:
    phone_connection = shopify_connection_for_phone(db, phone)
    if phone_connection:
        return phone_connection
    return db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.platform == "shopify", EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.desc())
    ).scalars().first()


def shopify_connection_for_phone(db: Session, phone: str | None) -> EcommerceConnection | None:
    normalized = _digits(phone)
    if not normalized:
        return None

    mapping = db.execute(
        select(ContactStoreMapping)
        .where(
            ContactStoreMapping.normalized_phone == normalized,
            ContactStoreMapping.status == "active",
        )
        .order_by(ContactStoreMapping.last_seen_at.desc(), ContactStoreMapping.updated_at.desc())
    ).scalars().first()
    if mapping:
        connection = db.get(EcommerceConnection, mapping.connection_id)
        if is_active_shopify_connection(connection):
            return connection

    orders = db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.phone.is_not(None))
        .order_by(EcommerceOrder.updated_at.desc())
        .limit(100)
    ).scalars().all()
    for order in orders:
        if _digits(order.phone) == normalized:
            connection = db.get(EcommerceConnection, order.connection_id)
            if is_active_shopify_connection(connection):
                return connection

    customers = db.execute(
        select(EcommerceCustomer)
        .where(EcommerceCustomer.phone.is_not(None))
        .order_by(EcommerceCustomer.updated_at.desc())
        .limit(100)
    ).scalars().all()
    for customer in customers:
        if _digits(customer.phone) == normalized:
            connection = db.get(EcommerceConnection, customer.connection_id)
            if is_active_shopify_connection(connection):
                return connection

    owner_connection = db.execute(
        select(EcommerceConnection)
        .where(EcommerceConnection.platform == "shopify", EcommerceConnection.status == "active")
        .order_by(EcommerceConnection.updated_at.desc())
    ).scalars().all()
    for connection in owner_connection:
        if _digits(connection.owner_phone) == normalized:
            return connection
    return None


def is_active_shopify_connection(connection: EcommerceConnection | None) -> bool:
    return bool(
        connection
        and connection.platform == "shopify"
        and connection.status == "active"
    )


def _digits(value: str | None) -> str:
    return re.sub(r"\D+", "", value or "")
