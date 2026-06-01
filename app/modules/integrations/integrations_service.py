import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import Integration, IntegrationStatus
from app.models.user import User
from app.modules.ecommerce.shared.token_service import decrypt_token, encrypt_token
from app.modules.audit import write_audit_log


def user_for_tenant(db: Session, tenant_id: str) -> User | None:
    try:
        return db.get(User, UUID(str(tenant_id)))
    except (TypeError, ValueError):
        return None


def serialize_integration(integration: Integration) -> dict:
    try:
        scopes = json.loads(integration.scopes or "[]")
    except json.JSONDecodeError:
        scopes = []

    return {
        "id": str(integration.id),
        "tenant_id": integration.tenant_id,
        "provider": integration.provider,
        "status": integration.status,
        "scopes": scopes if isinstance(scopes, list) else [],
        "provider_account_id": integration.provider_account_id,
        "display_name": integration.display_name,
        "expires_at": integration.expires_at,
        "created_at": integration.created_at,
        "updated_at": integration.updated_at,
    }


def list_integrations(db: Session, tenant_id: str) -> list[Integration]:
    return db.execute(
        select(Integration)
        .where(Integration.tenant_id == tenant_id)
        .order_by(Integration.created_at.desc())
    ).scalars().all()


def get_integration(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    provider_account_id: str | None = None,
) -> Integration | None:
    statement = select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == provider,
    )
    if provider_account_id:
        statement = statement.where(Integration.provider_account_id == provider_account_id)
    return db.execute(statement).scalars().first()


def upsert_integration(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    scopes: list[str] | None = None,
    access_token: str | None = None,
    refresh_token: str | None = None,
    provider_account_id: str | None = None,
    display_name: str | None = None,
    status: str = IntegrationStatus.CONNECTED,
) -> Integration:
    user = user_for_tenant(db, tenant_id)
    statement = select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.provider == provider,
    )
    if provider_account_id:
        statement = statement.where(Integration.provider_account_id == provider_account_id)

    integration = db.execute(statement).scalars().first()
    scopes_json = json.dumps(scopes or [])

    is_update = integration is not None
    if integration:
        integration.user_id = user.id if user else integration.user_id
        integration.status = status
        integration.scopes = scopes_json
        if access_token:
            integration.access_token = encrypt_token(access_token)
        if refresh_token:
            integration.refresh_token = encrypt_token(refresh_token)
        integration.provider_account_id = provider_account_id or integration.provider_account_id
        integration.display_name = display_name or integration.display_name
    else:
        integration = Integration(
            user_id=user.id if user else None,
            tenant_id=tenant_id,
            provider=provider,
            status=status,
            scopes=scopes_json,
            access_token=encrypt_token(access_token),
            refresh_token=encrypt_token(refresh_token),
            provider_account_id=provider_account_id,
            display_name=display_name,
        )
        db.add(integration)

    db.flush()
    write_audit_log(
        db,
        action="integration.updated" if is_update else "integration.connected",
        tenant_id=tenant_id,
        user_id=str(user.id) if user else None,
        entity_type="integration",
        entity_id=integration.id,
        metadata={
            "provider": provider,
            "status": status,
            "provider_account_id": provider_account_id,
            "display_name": display_name,
            "scopes": scopes or [],
            "tokens_changed": bool(access_token or refresh_token),
        },
    )
    db.commit()
    db.refresh(integration)
    return integration


def disconnect_integration(
    db: Session,
    *,
    tenant_id: str,
    provider: str,
    provider_account_id: str | None = None,
) -> Integration | None:
    integration = get_integration(
        db,
        tenant_id=tenant_id,
        provider=provider,
        provider_account_id=provider_account_id,
    )
    if not integration:
        return None

    integration.status = IntegrationStatus.DISCONNECTED
    write_audit_log(
        db,
        action="integration.disconnected",
        tenant_id=tenant_id,
        user_id=str(integration.user_id) if integration.user_id else None,
        entity_type="integration",
        entity_id=integration.id,
        metadata={
            "provider": provider,
            "provider_account_id": provider_account_id,
        },
    )
    db.commit()
    db.refresh(integration)
    return integration


def decrypted_integration_tokens(integration: Integration) -> dict[str, str | None]:
    return {
        "access_token": decrypt_token(integration.access_token),
        "refresh_token": decrypt_token(integration.refresh_token),
    }
