from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.knowledge import KnowledgeBase
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.modules.knowledge.knowledge_service import knowledge_context, save_knowledge_base


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    KnowledgeBase.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_return_policy_context_prioritizes_return_section():
    db = _session()
    save_knowledge_base(
        db,
        KnowledgeBaseRequest(
            company_name="Brand A",
            policies=(
                "Policy source: https://brand.example/policies/shipping-policy\n"
                "## Your cart is empty\n"
                "# Shipping policy\n"
                "Standard delivery takes 3-7 days.\n\n"
                "Policy source: https://brand.example/pages/return-exchange-policy\n"
                "# Return & Exchange Policy\n"
                "Wrong, damaged, defective, or missing-part items are eligible.\n"
                "Please email support within 48 hours of delivery with 4-5 images.\n"
            ),
        ),
        tenant_id="brand-a",
    )

    context = knowledge_context(db, "return policy kya hai", tenant_id="brand-a")

    assert "Return & Exchange Policy" in context
    assert "48 hours of delivery" in context
    assert "Your cart is empty" not in context
    assert context.find("Return & Exchange Policy") < context.find("Shipping policy")


def test_shopify_policy_noise_is_removed_before_storage():
    db = _session()
    save_knowledge_base(
        db,
        KnowledgeBaseRequest(
            company_name="The Home Senses",
            policies=(
                "Skip to content\n\nExtra 5% off on all Prepaid Orders\n\n"
                "The Home Senses\nHome Improvement\nKitchen\nLog in\nCart\n7\n7 items\n\n"
                "Return & Exchange Policy\n\nRETURN, EXCHANGE AND REFUND POLICY\n\n"
                "Wrong product, damaged product, genuine defect, or missing parts are eligible.\n"
                "Please raise a request at contact@thehomesenses.in within 48 hours of delivery.\n\n"
                "Cancellation Policy\n\nOrders can be cancelled before they are packaged for dispatch only.\n\n"
                "Shipping Policy\n\nStandard shipping across India would take 3-7 days depending on your pincode.\n\n"
                "Company\nSearch\nAbout Us\nPayment methods\n(c) 2026, The Home Senses Powered by Shopify"
            ),
        ),
        tenant_id="brand-a",
    )

    stored = db.query(KnowledgeBase).one().policies

    assert "Return & Exchange Policy" in stored
    assert "within 48 hours of delivery" in stored
    assert "Shipping Policy" in stored
    assert "Skip to content" not in stored
    assert "Home Improvement" not in stored
    assert "7 items" not in stored
    assert "Powered by Shopify" not in stored
