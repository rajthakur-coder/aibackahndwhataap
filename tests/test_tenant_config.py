from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.tenants import TenantConfig
from app.modules.tenants.tenant_schema import TenantConfigRequest
from app.modules.tenants.tenant_service import (
    seed_tenant_config,
    serialize_tenant_config,
    tenant_config_context,
    upsert_tenant_config,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TenantConfig.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()


def test_seed_tenant_config_uses_generic_template_not_brand_logic():
    db = _session()

    row = seed_tenant_config(db, tenant_id="brand-a", template="commerce")
    payload = serialize_tenant_config(row)

    assert payload["tenant_id"] == "brand-a"
    assert payload["brand_name"] == "Commerce Brand"
    assert "Best Sellers" in payload["categories"]
    assert payload["support_email"] == ""


def test_upsert_tenant_config_updates_partial_fields():
    db = _session()
    seed_tenant_config(db, tenant_id="brand-a", template="commerce")

    row = upsert_tenant_config(
        db,
        TenantConfigRequest(
            brand_name="Brand A",
            categories=["Bedding"],
            support_email="support@example.com",
        ),
        tenant_id="brand-a",
    )
    payload = serialize_tenant_config(row)

    assert payload["brand_name"] == "Brand A"
    assert payload["categories"] == ["Bedding"]
    assert payload["support_email"] == "support@example.com"
    assert payload["return_policy"]


def test_tenant_config_context_is_prompt_ready():
    db = _session()
    seed_tenant_config(db, tenant_id="brand-a", template="commerce")

    context = tenant_config_context(db, tenant_id="brand-a")

    assert "Brand: Commerce Brand" in context
    assert "Return policy:" in context
    assert "Discount rules:" in context
