from pydantic import TypeAdapter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ecommerce import EcommerceBundlePairing, EcommerceProduct
from app.models.knowledge import KnowledgeBase
from app.models.tenants import TenantConfig
from app.modules.headless.onboarding_assist_service import (
    BundleApplyRequest,
    BundleSuggestRequest,
    FAQAssistRequest,
    WebsiteAssistRequest,
    apply_bundle_suggestions,
    extract_faq_pairs,
    faq_onboarding_assist,
    save_website_onboarding_assist,
    suggest_bundle_pairings,
)
from app.modules.scraper.scraper_schema import ScraperResultOut
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        TenantConfig.__table__,
        KnowledgeBase.__table__,
        EcommerceProduct.__table__,
        EcommerceBundlePairing.__table__,
    ):
        table.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_website_assist_saves_brand_voice_and_knowledge_base():
    db = _session()
    url = TypeAdapter(WebsiteAssistRequest).validate_python({"website_url": "https://brand.example"}).website_url

    result = save_website_onboarding_assist(
        db,
        "brand-a",
        WebsiteAssistRequest(website_url=url),
        ScraperResultOut(
            company_name="Brand A",
            industry="Home Decor",
            about_company="Handcrafted decor for warm homes.",
            website_link="https://brand.example",
            target_demographics="urban homeowners",
        ),
        "success",
    )

    config = serialize_tenant_config(get_tenant_config(db, "brand-a"))
    kb = db.query(KnowledgeBase).one()
    assert result["saved"] is True
    assert config["brand_name"] == "Brand A"
    assert "WhatsApp commerce assistant for Brand A" in config["brand_voice_prompt"]
    assert config["metadata"]["onboarding"]["website_scrape"] is True
    assert kb.tenant_id == "brand-a"
    assert "What does Brand A sell" in kb.faqs


def test_faq_assist_extracts_csv_and_marks_onboarding():
    db = _session()

    result = faq_onboarding_assist(
        db,
        "brand-a",
        FAQAssistRequest(content='question,answer\nDo you ship?,"Yes, across India."'),
    )

    config = serialize_tenant_config(get_tenant_config(db, "brand-a"))
    assert result["saved"] is True
    assert result["faqs"][0]["question"] == "Do you ship?"
    assert config["metadata"]["onboarding"]["faq"] is True


def test_extract_faq_pairs_from_plain_text():
    result = extract_faq_pairs("Q: Can I return?\nA: Yes, if eligible.")

    assert result == [{"question": "Can I return?", "answer": "Yes, if eligible."}]


def test_bundle_assist_suggests_and_applies_pairings():
    db = _session()
    db.add_all(
        [
            EcommerceProduct(
                tenant_id="brand-a",
                connection_id=1,
                platform="shopify",
                external_id="p1",
                title="Linen Throw",
                sku="THROW-1",
                product_type="Throw",
                tags="home cozy gifting",
                collections="Living Room",
                price_min="1999",
            ),
            EcommerceProduct(
                tenant_id="brand-a",
                connection_id=1,
                platform="shopify",
                external_id="p2",
                title="Ceramic Vase",
                sku="VASE-1",
                product_type="Vase",
                tags="home decor gifting",
                collections="Living Room",
                price_min="1499",
            ),
        ]
    )
    db.commit()

    suggestions = suggest_bundle_pairings(db, "brand-a", BundleSuggestRequest(limit=1))
    applied = apply_bundle_suggestions(db, "brand-a", BundleApplyRequest(suggestions=suggestions))

    assert suggestions[0]["primary_sku"] in {"VASE-1", "THROW-1"}
    assert applied[0]["paired_skus"][0] in {"VASE-1", "THROW-1"}
    assert applied[0]["paired_skus"][0] != applied[0]["primary_sku"]
    assert serialize_tenant_config(get_tenant_config(db, "brand-a"))["metadata"]["onboarding"]["bundle_pairs"] is True
