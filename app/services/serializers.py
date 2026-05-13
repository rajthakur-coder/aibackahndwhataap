import json

from app.models.ecommerce import EcommerceConnection, EcommerceCustomer, EcommerceOrder, EcommerceProduct


def serialize_ecommerce_connection(connection: EcommerceConnection) -> dict:
    return {
        "id": connection.id,
        "name": connection.name,
        "platform": connection.platform,
        "store_url": connection.store_url,
        "store_name": connection.store_name,
        "myshopify_domain": connection.myshopify_domain,
        "shopify_shop_id": connection.shopify_shop_id,
        "currency": connection.currency,
        "timezone": connection.timezone,
        "owner_email": connection.owner_email,
        "owner_phone": connection.owner_phone,
        "plan_name": connection.plan_name,
        "webhook_status": connection.webhook_status,
        "status": connection.status,
        "has_access_token": bool(connection.access_token),
        "has_encrypted_access_token": bool(connection.encrypted_access_token),
        "has_consumer_key": bool(connection.consumer_key),
        "has_consumer_secret": bool(connection.consumer_secret),
        "last_sync_at": str(connection.last_sync_at) if connection.last_sync_at else None,
        "installed_at": str(connection.installed_at) if connection.installed_at else None,
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
        "tags": order.tags,
        "note": order.note,
        "status": order.status,
        "fulfillment_status": order.fulfillment_status,
        "financial_status": order.financial_status,
        "subtotal": order.subtotal,
        "total": order.total,
        "discounts": order.discounts,
        "tax": order.tax,
        "currency": order.currency,
        "payment_gateway": order.payment_gateway,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
        "courier_company": order.courier_company,
        "shipment_status": order.shipment_status,
        "delivery_status": order.delivery_status,
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
        "description_html": product.description_html,
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
        "seo_title": product.seo_title,
        "seo_description": product.seo_description,
        "updated_at": str(product.updated_at),
    }


def serialize_ecommerce_customer(customer: EcommerceCustomer) -> dict:
    try:
        addresses = json.loads(customer.addresses or "[]")
    except json.JSONDecodeError:
        addresses = []

    return {
        "id": customer.id,
        "connection_id": customer.connection_id,
        "platform": customer.platform,
        "external_id": customer.external_id,
        "shopify_customer_id": customer.shopify_customer_id,
        "name": customer.name,
        "phone": customer.phone,
        "email": customer.email,
        "total_orders": customer.total_orders,
        "total_spend": customer.total_spend,
        "tags": customer.tags,
        "addresses": addresses,
        "last_order_at": customer.last_order_at,
        "marketing_consent": customer.marketing_consent,
        "preferred_language": customer.preferred_language,
        "whatsapp_opt_in": customer.whatsapp_opt_in,
        "updated_at": str(customer.updated_at),
    }
