import json

from app.models.entities import EcommerceConnection, EcommerceOrder, EcommerceProduct


def serialize_ecommerce_connection(connection: EcommerceConnection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "platform": connection.platform,
        "store_url": connection.store_url,
        "status": connection.status,
        "has_access_token": bool(connection.access_token),
        "has_consumer_key": bool(connection.consumer_key),
        "has_consumer_secret": bool(connection.consumer_secret),
        "last_sync_at": str(connection.last_sync_at) if connection.last_sync_at else None,
        "created_at": str(connection.created_at),
    }


def serialize_ecommerce_order(order: EcommerceOrder) -> dict:
    try:
        items = json.loads(order.items or "[]")
    except json.JSONDecodeError:
        items = []

    return {
        "id": order.id,
        "connection_id": order.connection_id,
        "platform": order.platform,
        "external_id": order.external_id,
        "order_number": order.order_number,
        "phone": order.phone,
        "email": order.email,
        "customer_name": order.customer_name,
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "total": order.total,
        "currency": order.currency,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "items": items,
        "delivered_message_sent_at": (
            str(order.delivered_message_sent_at) if order.delivered_message_sent_at else None
        ),
        "updated_at": str(order.updated_at),
    }


def serialize_ecommerce_product(product: EcommerceProduct) -> dict:
    try:
        image_urls = json.loads(product.image_urls or "[]")
    except json.JSONDecodeError:
        image_urls = []

    return {
        "id": product.id,
        "connection_id": product.connection_id,
        "platform": product.platform,
        "external_id": product.external_id,
        "title": product.title,
        "handle": product.handle,
        "product_url": product.product_url,
        "description": product.description,
        "vendor": product.vendor,
        "product_type": product.product_type,
        "tags": product.tags,
        "status": product.status,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "currency": product.currency,
        "sku": product.sku,
        "inventory": product.inventory,
        "image_urls": image_urls,
        "updated_at": str(product.updated_at),
    }
