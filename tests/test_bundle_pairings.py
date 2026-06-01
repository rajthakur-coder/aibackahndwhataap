from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.ecommerce import EcommerceBundlePairing, EcommerceProduct
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingRequest
from app.modules.ecommerce.bundles.bundle_service import manual_bundle_products, upsert_bundle_pairing


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    EcommerceBundlePairing.__table__.create(bind=engine)
    EcommerceProduct.__table__.create(bind=engine)
    SessionLocal = sessionmaker(bind=engine, future=True)
    return SessionLocal()


def test_manual_bundle_pairing_returns_paired_products():
    db = _session()
    db.add(
        EcommerceProduct(
            tenant_id="brand-a",
            connection_id=1,
            platform="shopify",
            external_id="p2",
            title="Ceramic Vase",
            sku="VASE-1",
        )
    )
    db.commit()

    pairing = upsert_bundle_pairing(
        db,
        BundlePairingRequest(
            primary_sku="THROW-1",
            paired_skus=["VASE-1"],
            discount_type="percentage",
            discount_value="8",
        ),
        tenant_id="brand-a",
    )
    result = manual_bundle_products(db, "THROW-1", tenant_id="brand-a")

    assert pairing["primary_sku"] == "THROW-1"
    assert result["pairing"]["discount_value"] == "8"
    assert result["products"][0]["title"] == "Ceramic Vase"
