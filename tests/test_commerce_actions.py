from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.automation import AutomationEvent
from app.models.crm import AgentAction, HandoffTicket, Lead
from app.models.ecommerce import EcommerceCart, EcommerceConnection, EcommerceOrder, EcommerceProduct, EcommerceReturnRequest
from app.models.tenants import TenantConfig
from app.modules.ai.recommendations.sales_recommendations_service import (
    find_product_recommendations,
    find_top_selling_products,
)
from app.modules.ai.orchestrator.tool_executor import execute_tool


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    for table in (
        AutomationEvent.__table__,
        EcommerceCart.__table__,
        EcommerceConnection.__table__,
        EcommerceReturnRequest.__table__,
        EcommerceProduct.__table__,
        EcommerceOrder.__table__,
        HandoffTicket.__table__,
        AgentAction.__table__,
        Lead.__table__,
        TenantConfig.__table__,
    ):
        table.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()


def test_apply_discount_uses_tenant_rules():
    db = _session()
    db.add(
        TenantConfig(
            tenant_id="brand-a",
            brand_name="Brand A",
            discount_rules='[{"code": "FIRST10", "type": "percentage", "value": 10}]',
        )
    )
    db.add(EcommerceCart(tenant_id="brand-a", phone="919999999999", status="open", items="[]"))
    db.commit()

    result = execute_tool(
        db,
        "apply_discount",
        phone="919999999999",
        message="apply FIRST10",
        entities={"code": "FIRST10"},
        tenant_id="brand-a",
    )

    cart = db.query(EcommerceCart).one()
    assert result.status == "success"
    assert "FIRST10" in cart.metadata_json


def test_return_eligibility_uses_delivered_order_window():
    db = _session()
    db.add(
        EcommerceOrder(
            tenant_id="brand-a",
            connection_id=1,
            platform="shopify",
            external_id="o1",
            order_number="#HS-1",
            phone="919999999999",
            status="delivered",
            delivery_status="delivered",
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    result = execute_tool(
        db,
        "get_return_eligibility",
        phone="919999999999",
        message="return order #HS-1",
        entities={"order_id": "#HS-1"},
        tenant_id="brand-a",
    )

    assert result.status == "success"
    assert result.data["eligible"] is True


def test_order_lookup_is_scoped_to_explicit_tenant():
    db = _session()
    db.add(
        EcommerceOrder(
            tenant_id="brand-a",
            connection_id=1,
            platform="shopify",
            external_id="a1",
            order_number="#HS-1",
            phone="919999999999",
            status="delivered",
            delivery_status="delivered",
            total="1000",
        )
    )
    db.add(
        EcommerceOrder(
            tenant_id="brand-b",
            connection_id=2,
            platform="shopify",
            external_id="b1",
            order_number="#HS-1",
            phone="919999999999",
            status="in_transit",
            delivery_status="in_transit",
            total="2000",
        )
    )
    db.commit()

    result = execute_tool(
        db,
        "get_order_status",
        phone="919999999999",
        message="status for order #HS-1",
        entities={"order_id": "#HS-1"},
        tenant_id="brand-b",
    )

    assert result.status == "success"
    assert result.data["id"] == 2
    assert result.data["status"] == "in_transit"


def test_catalog_recommendations_are_tenant_scoped_and_provider_neutral():
    db = _session()
    db.add(
        EcommerceProduct(
            tenant_id="brand-a",
            connection_id=1,
            platform="woocommerce",
            external_id="woo-1",
            title="Ceramic Bowl",
            sku="BOWL-1",
            product_type="Tableware",
            tags="home ceramic",
            price_min="999",
        )
    )
    db.add(
        EcommerceProduct(
            tenant_id="brand-b",
            connection_id=2,
            platform="shopify",
            external_id="shop-1",
            title="Running Shoes",
            sku="SHOE-1",
            product_type="Footwear",
            tags="shoe sneaker",
            price_min="2999",
        )
    )
    db.commit()

    results = find_product_recommendations(db, "show ceramic products", tenant_id="brand-a")

    assert [item["title"] for item in results] == ["Ceramic Bowl"]
    assert results[0]["platform"] == "woocommerce"


def test_top_selling_products_are_tenant_scoped():
    db = _session()
    db.add(
        EcommerceProduct(
            tenant_id="brand-a",
            connection_id=1,
            platform="woocommerce",
            external_id="p-a",
            title="Ceramic Bowl",
            sku="BOWL-1",
        )
    )
    db.add(
        EcommerceProduct(
            tenant_id="brand-b",
            connection_id=2,
            platform="shopify",
            external_id="p-b",
            title="Running Shoes",
            sku="SHOE-1",
        )
    )
    db.add(
        EcommerceOrder(
            tenant_id="brand-a",
            connection_id=1,
            platform="woocommerce",
            external_id="o-a",
            order_number="#A1",
            items='[{"name": "Ceramic Bowl", "sku": "BOWL-1", "product_id": "p-a", "quantity": 3}]',
        )
    )
    db.add(
        EcommerceOrder(
            tenant_id="brand-b",
            connection_id=2,
            platform="shopify",
            external_id="o-b",
            order_number="#B1",
            items='[{"name": "Running Shoes", "sku": "SHOE-1", "product_id": "p-b", "quantity": 9}]',
        )
    )
    db.commit()

    results = find_top_selling_products(db, tenant_id="brand-a")

    assert [item["title"] for item in results] == ["Ceramic Bowl"]
    assert results[0]["platform"] == "woocommerce"


def test_support_ticket_is_tenant_scoped():
    db = _session()

    result = execute_tool(
        db,
        "create_support_ticket",
        phone="919999999999",
        message="need human support",
        entities={"issue": "need human support"},
        tenant_id="brand-a",
    )

    ticket = db.query(HandoffTicket).one()
    assert result.status == "success"
    assert ticket.tenant_id == "brand-a"


def test_bulk_lead_is_logged():
    db = _session()

    result = execute_tool(
        db,
        "log_bulk_lead",
        phone="919999999999",
        message="bulk gifting for wedding",
        entities={"name": "Asha", "email": "asha@example.com", "qty": "100", "timeline": "2 weeks"},
        tenant_id="brand-a",
    )

    lead = db.query(Lead).one()
    assert result.status == "success"
    assert result.data["lead_id"] == lead.id
    assert lead.tenant_id == "brand-a"
    assert lead.intent == "bulk_gifting"
