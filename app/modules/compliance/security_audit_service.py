from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.ecommerce import EcommerceConnection
from app.models.whatsapp import WhatsappCredential
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def tenant_security_audit(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    checks = [
        _whatsapp_credentials_check(db, tenant_id),
        _ecommerce_credentials_check(db, tenant_id),
        _webhook_signature_check(),
        _default_tenant_check(db, tenant_id),
    ]
    failed = [check for check in checks if check["status"] != "pass"]
    return {
        "tenant_id": tenant_id,
        "status": "pass" if not failed else "needs_attention",
        "checks": checks,
        "failed_count": len(failed),
    }


def _whatsapp_credentials_check(db: Session, tenant_id: str) -> dict:
    rows = db.execute(select(WhatsappCredential).where(WhatsappCredential.tenant_id == tenant_id)).scalars().all()
    active = [row for row in rows if row.status == "active"]
    issues = []
    if len(active) != 1:
        issues.append("Exactly one active WhatsApp credential should be configured per tenant.")
    for row in active:
        if not row.phone_number_id or not row.waba_id:
            issues.append("Active WhatsApp credential must include phone_number_id and waba_id.")
        if not row.token:
            issues.append("Active WhatsApp credential is missing encrypted access token.")
    return {
        "name": "whatsapp_credentials",
        "status": "pass" if not issues else "fail",
        "active_count": len(active),
        "issues": issues,
    }


def _ecommerce_credentials_check(db: Session, tenant_id: str) -> dict:
    rows = db.execute(select(EcommerceConnection).where(EcommerceConnection.tenant_id == tenant_id)).scalars().all()
    issues = []
    for row in rows:
        if row.status != "active":
            continue
        if row.platform == "shopify" and not (row.encrypted_access_token or row.access_token):
            issues.append(f"Shopify connection {row.id} is missing access token.")
        if row.platform == "shopify" and row.access_token and not row.encrypted_access_token:
            issues.append(f"Shopify connection {row.id} still has plaintext access_token without encrypted_access_token.")
        if row.platform == "woocommerce" and not (row.consumer_key and row.consumer_secret):
            issues.append(f"WooCommerce connection {row.id} is missing API credentials.")
    return {
        "name": "ecommerce_credentials",
        "status": "pass" if not issues else "fail",
        "active_count": len([row for row in rows if row.status == "active"]),
        "issues": issues,
    }


def _webhook_signature_check() -> dict:
    issues = []
    if not (settings.WHATSAPP_WEBHOOK_APP_SECRET or settings.META_APP_SECRET):
        issues.append("WhatsApp webhook signature secret is not configured.")
    if not settings.SHOPIFY_WEBHOOK_SECRET:
        issues.append("Shopify webhook signature secret is not configured.")
    return {
        "name": "webhook_signatures",
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }


def _default_tenant_check(db: Session, tenant_id: str) -> dict:
    issues = []
    if tenant_id == DEFAULT_TENANT_ID:
        issues.append("Authenticated tenant resolved to default; production tenants must be explicit.")
    default_whatsapp = db.execute(select(WhatsappCredential.id).where(WhatsappCredential.tenant_id == DEFAULT_TENANT_ID)).first()
    default_ecommerce = db.execute(select(EcommerceConnection.id).where(EcommerceConnection.tenant_id == DEFAULT_TENANT_ID)).first()
    if default_whatsapp:
        issues.append("Default tenant has WhatsApp credentials; migrate them to a real tenant.")
    if default_ecommerce:
        issues.append("Default tenant has ecommerce credentials; migrate them to a real tenant.")
    return {
        "name": "default_tenant_guard",
        "status": "pass" if not issues else "fail",
        "issues": issues,
    }
