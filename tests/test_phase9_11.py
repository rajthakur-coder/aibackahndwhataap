from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.automation import BroadcastCampaign
from app.models.compliance import CSATResponse, CustomerConsent, TenantCustomTool
from app.models.crm import AgentAction, HandoffTicket, Lead
from app.models.ecommerce import EcommerceCart, EcommerceOrder, EcommerceReturnRequest
from app.models.whatsapp import Message, WebhookEvent, WhatsappInteractionEvent
from app.modules.analytics.analytics_service import commerce_dashboard, record_csat
from app.modules.automation.broadcast_service import create_broadcast_campaign, list_broadcast_campaigns
from app.modules.compliance.compliance_service import capture_consent, delete_customer_data, export_customer_data
from app.modules.compliance.pii import redact_pii
from app.modules.compliance.template_compliance import check_template_compliance
from app.modules.headless.custom_tool_service import execute_custom_tool, list_custom_tools, upsert_custom_tool


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        Message.__table__,
        WebhookEvent.__table__,
        WhatsappInteractionEvent.__table__,
        AgentAction.__table__,
        HandoffTicket.__table__,
        Lead.__table__,
        EcommerceCart.__table__,
        EcommerceOrder.__table__,
        EcommerceReturnRequest.__table__,
        CustomerConsent.__table__,
        CSATResponse.__table__,
        TenantCustomTool.__table__,
        BroadcastCampaign.__table__,
    ):
        table.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_dashboard_and_csat_metrics():
    db = _session()
    now = datetime.utcnow()
    db.add(Message(tenant_id="brand-a", phone="91", message="hi", direction="incoming", created_at=now))
    db.add(Message(tenant_id="brand-a", phone="91", message="hello", direction="outgoing", created_at=now + timedelta(seconds=1)))
    db.add(EcommerceCart(tenant_id="brand-a", phone="91", status="checkout_ready", created_at=now))
    record_csat(db, "brand-a", "91", 5, "good")

    result = commerce_dashboard(db, "brand-a")

    assert result["first_response_time_seconds"] == 1
    assert result["csat_average"] == 5
    assert result["cart_recovery_rate"] == 1


def test_compliance_export_delete_and_pii_redaction():
    db = _session()
    db.add(Message(tenant_id="brand-a", phone="91", message="mail me at a@test.com", direction="incoming"))
    db.add(WhatsappInteractionEvent(tenant_id="brand-a", phone="91", event_type="click"))
    db.add(WhatsappInteractionEvent(tenant_id="brand-b", phone="91", event_type="click"))
    db.commit()
    capture_consent(db, "brand-a", "91", "marketing", "granted")

    exported = export_customer_data(db, "brand-a", "91")
    deleted = delete_customer_data(db, "brand-a", "91")

    assert exported["messages"]
    assert deleted["deleted"]["messages"] == 1
    assert deleted["deleted"]["whatsapp_interaction_events"] == 1
    assert db.query(WhatsappInteractionEvent).filter(WhatsappInteractionEvent.tenant_id == "brand-b").count() == 1
    assert redact_pii("call +919999999999 and mail a@test.com") == "call [phone]and mail [email]"


def test_template_compliance_and_custom_tools():
    db = _session()
    bad = check_template_compliance({"category": "UTILITY", "components": [{"text": "limited time discount"}]})
    tool = upsert_custom_tool(db, "brand-a", {"name": "recommend_scent", "input_schema": {"season": "string"}})
    execute_custom_tool(db, "brand-a", "recommend_scent", phone="91", message="recommend", entities={"season": "winter"})

    assert bad["ok"] is False
    assert tool["name"] == "recommend_scent"
    assert list_custom_tools(db, "brand-a")[0]["input_schema"]["season"] == "string"
    assert db.query(AgentAction).one().tenant_id == "brand-a"


def test_broadcast_campaigns_are_tenant_scoped():
    db = _session()

    created = create_broadcast_campaign(
        db,
        "brand-a",
        {"name": "Weekend Sale", "template": "weekend_sale", "audience": ["911", "912"], "variables": {"offer": "10%"}},
    )

    assert created["tenant_id"] == "brand-a"
    assert created["status"] == "queued"
    assert created["audience"] == ["911", "912"]
    assert list_broadcast_campaigns(db, "brand-a")[0]["template"] == "weekend_sale"
    assert list_broadcast_campaigns(db, "brand-b") == []
