from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ecommerce import (
    EcommerceBundlePairing,
    EcommerceConnection,
    EcommerceProduct,
    ShopifyCatalogCollection,
    ShopifyCatalogDefaultCategory,
)
from app.models.knowledge import KnowledgeBase
from app.models.tenants import TenantConfig
from app.models.whatsapp import WhatsappCredential, WhatsappTemplate
from app.modules.onboarding.onboarding_service import go_live_readiness, mark_go_live, onboarding_wizard, update_onboarding_step


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        TenantConfig.__table__,
        WhatsappCredential.__table__,
        EcommerceConnection.__table__,
        EcommerceProduct.__table__,
        ShopifyCatalogCollection.__table__,
        ShopifyCatalogDefaultCategory.__table__,
        KnowledgeBase.__table__,
        EcommerceBundlePairing.__table__,
        WhatsappTemplate.__table__,
    ):
        table.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_onboarding_wizard_detects_required_integrations_and_next_step():
    db = _session()
    db.add(TenantConfig(tenant_id="brand-a", brand_name="Brand A", brand_voice_prompt="Voice", return_policy="7 days"))
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="123", waba_id="waba", status="active"))
    db.add(EcommerceConnection(tenant_id="brand-a", name="Shop", platform="shopify", store_url="shop.myshopify.com", status="active"))
    db.add(EcommerceProduct(tenant_id="brand-a", connection_id=1, platform="shopify", external_id="p1", title="Lamp", sku="LAMP"))
    db.add(KnowledgeBase(tenant_id="brand-a", website_link="https://brand.example", company_name="Brand A", faqs="Q: Hi\nA: Hello"))
    db.commit()

    wizard = onboarding_wizard(db, "brand-a")

    completed = {step["key"]: step["completed"] for step in wizard["steps"]}
    assert completed["connect_whatsapp"] is True
    assert completed["connect_oms"] is True
    assert completed["import_catalog"] is True
    assert completed["brand_voice"] is True
    assert completed["faq"] is True
    assert completed["policies"] is True
    assert wizard["next_step"]["key"] == "discounts"
    assert wizard["next_required_step"]["key"] == "preview_test"


def test_onboarding_go_live_requires_preview_then_marks_live():
    db = _session()
    db.add(TenantConfig(tenant_id="brand-a", brand_name="Brand A", brand_voice_prompt="Voice", return_policy="7 days"))
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="123", waba_id="waba", status="active"))
    db.add(EcommerceConnection(tenant_id="brand-a", name="Shop", platform="shopify", store_url="shop.myshopify.com", status="active"))
    db.add(EcommerceProduct(tenant_id="brand-a", connection_id=1, platform="shopify", external_id="p1", title="Lamp", sku="LAMP"))
    db.add(KnowledgeBase(tenant_id="brand-a", website_link="https://brand.example", company_name="Brand A", faqs="Q: Hi\nA: Hello"))
    db.commit()

    assert go_live_readiness(db, "brand-a")["ready"] is False

    update_onboarding_step(db, "brand-a", "preview_test", "completed", {"channel": "sandbox"})
    readiness = go_live_readiness(db, "brand-a")
    live = mark_go_live(db, "brand-a")

    assert readiness["ready"] is True
    assert {step["key"]: step["completed"] for step in live["steps"]}["go_live"] is True


def test_onboarding_catalog_complete_for_live_shopify_selected_collections():
    db = _session()
    db.add(TenantConfig(tenant_id="brand-a", brand_name="Brand A", brand_voice_prompt="Voice", return_policy="7 days"))
    db.add(WhatsappCredential(tenant_id="brand-a", phone_number_id="123", waba_id="waba", status="active"))
    db.add(EcommerceConnection(id=7, tenant_id="brand-a", name="Shop", platform="shopify", store_url="shop.myshopify.com", status="active"))
    db.add(
        ShopifyCatalogCollection(
            tenant_id="brand-a",
            connection_id=7,
            shopify_collection_id="330769727648",
            title="Top Selling",
            product_count=29,
            visible="true",
            sort_order=1,
        )
    )
    db.add(KnowledgeBase(tenant_id="brand-a", website_link="https://brand.example", company_name="Brand A", faqs="Q: Hi\nA: Hello"))
    db.commit()

    completed = {step["key"]: step["completed"] for step in onboarding_wizard(db, "brand-a")["steps"]}

    assert completed["connect_oms"] is True
    assert completed["import_catalog"] is True
