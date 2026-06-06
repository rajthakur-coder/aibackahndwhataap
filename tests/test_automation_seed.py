from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.automation import MessageTemplate
from app.modules.automation.rules.seed_service import _ensure_message_template
from app.shared.tenant import reset_current_tenant_id, set_current_tenant_id


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    MessageTemplate.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_default_template_seed_is_tenant_scoped_and_idempotent():
    db = _session()
    payload = {
        "template_name": "order_confirmation",
        "message_body": "Hi {{customer_name}}",
        "body_variable_order": ["customer_name"],
    }

    token = set_current_tenant_id("brand-a")
    try:
        brand_a, created_a, updated_a = _ensure_message_template(db, payload)
    finally:
        reset_current_tenant_id(token)

    token = set_current_tenant_id("brand-b")
    try:
        brand_b, created_b, updated_b = _ensure_message_template(db, payload)
    finally:
        reset_current_tenant_id(token)

    token = set_current_tenant_id("brand-b")
    try:
        brand_b_again, created_again, updated_again = _ensure_message_template(db, payload)
    finally:
        reset_current_tenant_id(token)

    assert created_a is True
    assert updated_a is False
    assert brand_a.tenant_id == "brand-a"
    assert brand_a.name == "order_confirmation"
    assert created_b is True
    assert updated_b is False
    assert brand_b.tenant_id == "brand-b"
    assert brand_b.name == "order_confirmation"
    assert brand_b.provider_template_name == "order_confirmation"
    assert brand_b_again.id == brand_b.id
    assert created_again is False
    assert updated_again is False
