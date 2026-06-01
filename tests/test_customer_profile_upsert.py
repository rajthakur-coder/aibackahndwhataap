from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.crm import CustomerProfile
from app.modules.crm.agent.action_service import _get_or_create_profile
from app.shared.tenant import reset_current_tenant_id, set_current_tenant_id


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    CustomerProfile.__table__.create(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE UNIQUE INDEX ux_customer_profiles_phone_legacy ON customer_profiles (phone)"))
    return sessionmaker(bind=engine, future=True)()


def test_get_or_create_profile_reuses_existing_phone_with_legacy_unique_index():
    db = _session()
    db.add(CustomerProfile(tenant_id="brand-a", phone="919516615793", status="active"))
    db.commit()

    token = set_current_tenant_id("brand-b")
    try:
        profile = _get_or_create_profile(db, "919516615793")
    finally:
        reset_current_tenant_id(token)

    assert profile.phone == "919516615793"
    assert db.query(CustomerProfile).count() == 1
