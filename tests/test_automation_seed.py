from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.automation import MessageTemplate
from app.modules.automation.rules.seed_service import _ensure_message_template
from app.shared.tenant import reset_current_tenant_id, set_current_tenant_id


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    MessageTemplate.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_default_template_seed_uses_tenant_safe_name_when_global_name_exists():
    db = _session()
    db.add(
        MessageTemplate(
            tenant_id="brand-a",
            name="order_confirmation",
            body="Existing",
            provider_template_name="order_confirmation",
        )
    )
    db.commit()

    token = set_current_tenant_id("brand-b")
    try:
        row, created, updated = _ensure_message_template(
            db,
            {
                "template_name": "order_confirmation",
                "message_body": "Hi {{customer_name}}",
                "body_variable_order": ["customer_name"],
            },
        )
    finally:
        reset_current_tenant_id(token)

    assert created is True
    assert updated is False
    assert row.tenant_id == "brand-b"
    assert row.name == "brand-b:order_confirmation"
    assert row.provider_template_name == "order_confirmation"
