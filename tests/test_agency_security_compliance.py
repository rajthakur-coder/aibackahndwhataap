import hmac
import hashlib

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.compliance import CustomerConsent, DataPrincipalRequestLog
from app.models.ecommerce import EcommerceConnection
from app.models.tenants import AgencyTenantAccess, TenantConfig
from app.models.whatsapp import WhatsappCredential
from app.modules.compliance.compliance_service import create_data_principal_request, list_data_principal_requests, resolve_data_principal_request
from app.modules.compliance.security_audit_service import tenant_security_audit
from app.modules.compliance.tenant_isolation_audit_service import tenant_isolation_audit
from app.modules.tenants.agency_service import agency_overview, upsert_agency_client, white_label_profile
from app.modules.whatsapp.webhooks.events.event_service import verify_meta_webhook_signature


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        AgencyTenantAccess.__table__,
        TenantConfig.__table__,
        DataPrincipalRequestLog.__table__,
        CustomerConsent.__table__,
        WhatsappCredential.__table__,
        EcommerceConnection.__table__,
    ):
        table.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_agency_client_and_white_label_profile():
    db = _session()
    db.add(TenantConfig(tenant_id="brand-a", brand_name="Brand A"))
    db.commit()

    created = upsert_agency_client(
        db,
        "agency-a",
        {
            "client_tenant_id": "brand-a",
            "white_label_name": "Agency Desk",
            "white_label_domain": "desk.example.com",
            "support_email": "support@example.com",
        },
    )
    overview = agency_overview(db, "agency-a")
    profile = white_label_profile(db, "brand-a")

    assert created["client_brand_name"] == "Brand A"
    assert db.query(AgencyTenantAccess).one().tenant_id == "agency-a"
    assert overview["active_client_count"] == 1
    assert profile["agency_tenant_id"] == "agency-a"
    assert profile["white_label"]["domain"] == "desk.example.com"


def test_data_principal_request_lifecycle():
    db = _session()

    created = create_data_principal_request(db, "brand-a", "919999999999", "export", "buyer@example.com")
    resolved = resolve_data_principal_request(db, "brand-a", created["id"], "completed", "done")
    rows = list_data_principal_requests(db, "brand-a")

    assert created["status"] == "received"
    assert resolved["status"] == "completed"
    assert resolved["completed_at"]
    assert rows[0]["id"] == created["id"]


def test_security_audit_flags_missing_and_plaintext_credentials():
    db = _session()
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="123", waba_id="waba", token="encrypted", status="active"))
    db.add(EcommerceConnection(tenant_id="brand-a", name="Shop", platform="shopify", store_url="shop.myshopify.com", access_token="plain", status="active"))
    db.commit()

    result = tenant_security_audit(db, "brand-a")

    assert result["status"] == "needs_attention"
    assert any(check["name"] == "ecommerce_credentials" and check["issues"] for check in result["checks"])


def test_meta_webhook_signature_verification(monkeypatch):
    body = b'{"ok":true}'
    secret = "meta-secret"
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    monkeypatch.setattr("app.modules.whatsapp.webhooks.events.event_service.settings.WHATSAPP_WEBHOOK_APP_SECRET", secret)
    monkeypatch.setattr("app.modules.whatsapp.webhooks.events.event_service.settings.META_APP_SECRET", "")

    assert verify_meta_webhook_signature(body, signature) is True
    assert verify_meta_webhook_signature(body, "sha256=bad") is False


def test_tenant_isolation_audit_has_no_missing_tenant_id_tables():
    db = _session()

    result = tenant_isolation_audit(db)

    assert result["status"] == "pass"
    assert result["failed_count"] == 0
    assert not [row for row in result["tables"] if "missing_tenant_id" in row["issues"]]
