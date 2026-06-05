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
