from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceConnection
from app.models.integration import IntegrationProvider
from app.modules.ecommerce.shared.token_service import (
    encrypt_token as _encrypt_token,
)
from app.modules.audit import write_audit_log
from app.modules.integrations.integrations_service import upsert_integration

SUPPORTED_PLATFORMS = {"shopify", "woocommerce"}


def _clean_platform(platform: str) -> str:
    value = platform.strip().lower()
    if value not in SUPPORTED_PLATFORMS:
        raise ValueError("Platform must be shopify or woocommerce")
    return value

def _normalize_store_url(store_url: str, platform: str) -> str:
    value = store_url.strip().rstrip("/")
    if not value:
        raise ValueError("Store URL is required")

    if platform == "shopify":
        value = value.replace("https://", "").replace("http://", "").strip("/")
        return value

    if not urlparse(value).scheme:
        value = f"https://{value}"
    return value


def _bootstrap_shopify_connection(db: Session, connection: EcommerceConnection) -> None:
    from app.modules.ecommerce.providers.shopify.sync_service import bootstrap_shopify_connection

    bootstrap_shopify_connection(db, connection)


def create_connection(
    db: Session,
    name: str,
    platform: str,
    store_url: str,
    access_token: str | None = None,
    consumer_key: str | None = None,
    consumer_secret: str | None = None,
    tenant_id: str = "default",
    run_bootstrap: bool = True,
) -> EcommerceConnection:
    platform = _clean_platform(platform)
    store_url = _normalize_store_url(store_url, platform)

    if platform == "shopify" and not access_token:
        raise ValueError("Shopify access token is required")
    if platform == "woocommerce" and (not consumer_key or not consumer_secret):
        raise ValueError("WooCommerce consumer key and consumer secret are required")

    existing = db.execute(
        select(EcommerceConnection)
        .where(
            EcommerceConnection.platform == platform,
            EcommerceConnection.store_url == store_url,
        )
    ).scalars().first()
    if existing:
        existing.tenant_id = tenant_id or existing.tenant_id
        existing.name = name.strip() or existing.name or store_url
        existing.status = "active" if run_bootstrap else "syncing"
        if platform == "shopify":
            existing.myshopify_domain = existing.myshopify_domain or store_url
        if access_token:
            existing.access_token = None
            existing.encrypted_access_token = _encrypt_token(access_token)
        if consumer_key:
            existing.consumer_key = consumer_key
        if consumer_secret:
            existing.consumer_secret = consumer_secret
        integration = _upsert_ecommerce_integration(
            db,
            tenant_id=existing.tenant_id,
            platform=platform,
            store_url=store_url,
            name=existing.name,
            access_token=access_token or existing.encrypted_access_token or existing.access_token,
            consumer_key=consumer_key or existing.consumer_key,
            consumer_secret=consumer_secret or existing.consumer_secret,
        )
        existing.integration_id = integration.id
        write_audit_log(
            db,
            action="ecommerce.connection_updated",
            tenant_id=existing.tenant_id,
            entity_type="ecommerce_connection",
            entity_id=existing.id,
            metadata={
                "platform": platform,
                "store_url": store_url,
                "status": existing.status,
                "token_changed": bool(access_token or consumer_key or consumer_secret),
            },
        )
        db.commit()
        db.refresh(existing)
        if platform == "shopify" and run_bootstrap:
            _bootstrap_shopify_connection(db, existing)
        return existing

    connection = EcommerceConnection(
        tenant_id=tenant_id,
        name=name.strip() or store_url,
        platform=platform,
        store_url=store_url,
        status="syncing" if platform == "shopify" and not run_bootstrap else "active",
        myshopify_domain=store_url if platform == "shopify" else None,
        access_token=None if platform == "shopify" else access_token,
        encrypted_access_token=_encrypt_token(access_token),
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    integration = _upsert_ecommerce_integration(
        db,
        tenant_id=tenant_id,
        platform=platform,
        store_url=store_url,
        name=connection.name,
        access_token=connection.encrypted_access_token or connection.access_token,
        consumer_key=consumer_key,
        consumer_secret=consumer_secret,
    )
    connection.integration_id = integration.id
    write_audit_log(
        db,
        action="ecommerce.connection_created",
        tenant_id=tenant_id,
        entity_type="ecommerce_connection",
        entity_id=connection.id,
        metadata={
            "platform": platform,
            "store_url": store_url,
            "status": connection.status,
        },
    )
    db.commit()
    db.refresh(connection)
    if platform == "shopify" and run_bootstrap:
        _bootstrap_shopify_connection(db, connection)
    return connection


def _upsert_ecommerce_integration(
    db: Session,
    *,
    tenant_id: str,
    platform: str,
    store_url: str,
    name: str,
    access_token: str | None,
    consumer_key: str | None,
    consumer_secret: str | None,
):
    provider = IntegrationProvider.SHOPIFY if platform == "shopify" else IntegrationProvider.WOOCOMMERCE
    scopes = []
    if platform == "shopify":
        from app.modules.ecommerce.providers.shopify.http_client import required_shopify_scopes

        scopes = required_shopify_scopes()
    else:
        scopes = ["read_products", "read_orders", "read_customers", "write_webhooks"]

    token = access_token if platform == "shopify" else consumer_key
    refresh_token = None if platform == "shopify" else consumer_secret
    return upsert_integration(
        db,
        tenant_id=tenant_id,
        provider=provider,
        scopes=scopes,
        access_token=token,
        refresh_token=refresh_token,
        provider_account_id=store_url,
        display_name=name,
    )


def update_connection(
    db: Session,
    connection: EcommerceConnection,
    name: str | None = None,
    store_url: str | None = None,
    access_token: str | None = None,
    consumer_key: str | None = None,
    consumer_secret: str | None = None,
    status: str | None = None,
) -> EcommerceConnection:
    if name is not None:
        connection.name = name.strip() or connection.name
    if store_url is not None:
        connection.store_url = _normalize_store_url(store_url, connection.platform)
    if access_token:
        connection.access_token = None
        connection.encrypted_access_token = _encrypt_token(access_token)
    if consumer_key:
        connection.consumer_key = consumer_key
    if consumer_secret:
        connection.consumer_secret = consumer_secret
    if status is not None:
        connection.status = status

    if access_token or consumer_key or consumer_secret or name is not None or store_url is not None or status is not None:
        integration = _upsert_ecommerce_integration(
            db,
            tenant_id=connection.tenant_id,
            platform=connection.platform,
            store_url=connection.store_url,
            name=connection.name,
            access_token=connection.encrypted_access_token or connection.access_token,
            consumer_key=connection.consumer_key,
            consumer_secret=connection.consumer_secret,
        )
        connection.integration_id = integration.id

    write_audit_log(
        db,
        action="ecommerce.connection_updated",
        tenant_id=connection.tenant_id,
        entity_type="ecommerce_connection",
        entity_id=connection.id,
        metadata={
            "platform": connection.platform,
            "store_url": connection.store_url,
            "status": connection.status,
            "token_changed": bool(access_token or consumer_key or consumer_secret),
            "name_changed": name is not None,
            "store_url_changed": store_url is not None,
        },
    )
    db.commit()
    db.refresh(connection)
    return connection
