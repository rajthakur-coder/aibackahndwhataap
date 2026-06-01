import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceBundlePairing, EcommerceProduct
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingPatchRequest, BundlePairingRequest
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def list_bundle_pairings(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id)
    rows = db.execute(
        select(EcommerceBundlePairing)
        .where(EcommerceBundlePairing.tenant_id == tenant_id)
        .order_by(EcommerceBundlePairing.updated_at.desc())
    ).scalars().all()
    return [serialize_bundle_pairing(row) for row in rows]


def upsert_bundle_pairing(db: Session, data: BundlePairingRequest, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    primary_sku = data.primary_sku.strip()
    row = db.execute(
        select(EcommerceBundlePairing).where(
            EcommerceBundlePairing.tenant_id == tenant_id,
            EcommerceBundlePairing.primary_sku == primary_sku,
        )
    ).scalars().first()
    if not row:
        row = EcommerceBundlePairing(tenant_id=tenant_id, primary_sku=primary_sku)
        db.add(row)
    row.paired_skus = _json_dumps(data.paired_skus)
    row.discount_type = data.discount_type
    row.discount_value = data.discount_value
    row.status = data.status
    row.notes = data.notes
    db.commit()
    db.refresh(row)
    return serialize_bundle_pairing(row)


def patch_bundle_pairing(db: Session, pairing_id: int, data: BundlePairingPatchRequest, tenant_id: str) -> dict | None:
    row = _pairing_by_id(db, pairing_id, tenant_id)
    if not row:
        return None
    payload = data.model_dump(exclude_unset=True)
    if "paired_skus" in payload:
        row.paired_skus = _json_dumps(payload["paired_skus"] or [])
    for field in ("discount_type", "discount_value", "status", "notes"):
        if field in payload:
            setattr(row, field, payload[field])
    db.commit()
    db.refresh(row)
    return serialize_bundle_pairing(row)


def delete_bundle_pairing(db: Session, pairing_id: int, tenant_id: str) -> bool:
    row = _pairing_by_id(db, pairing_id, tenant_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def manual_bundle_products(db: Session, sku: str | None, tenant_id: str = DEFAULT_TENANT_ID) -> dict | None:
    sku = (sku or "").strip()
    if not sku:
        return None
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(
        select(EcommerceBundlePairing).where(
            EcommerceBundlePairing.tenant_id == tenant_id,
            EcommerceBundlePairing.primary_sku == sku,
            EcommerceBundlePairing.status == "active",
        )
    ).scalars().first()
    if not row:
        return None
    paired_skus = _json_loads(row.paired_skus, [])
    products = db.execute(
        select(EcommerceProduct).where(
            EcommerceProduct.tenant_id == tenant_id,
            EcommerceProduct.sku.in_(paired_skus),
        )
    ).scalars().all()
    return {
        "pairing": serialize_bundle_pairing(row),
        "products": [_product_dict(product) for product in products],
    }


def serialize_bundle_pairing(row: EcommerceBundlePairing) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "primary_sku": row.primary_sku,
        "paired_skus": _json_loads(row.paired_skus, []),
        "discount_type": row.discount_type,
        "discount_value": row.discount_value,
        "status": row.status,
        "notes": row.notes,
    }


def _pairing_by_id(db: Session, pairing_id: int, tenant_id: str) -> EcommerceBundlePairing | None:
    return db.execute(
        select(EcommerceBundlePairing).where(
            EcommerceBundlePairing.tenant_id == normalize_tenant_id(tenant_id),
            EcommerceBundlePairing.id == pairing_id,
        )
    ).scalars().first()


def _product_dict(product: EcommerceProduct) -> dict:
    image_urls = _json_loads(product.image_urls, [])
    return {
        "title": product.title,
        "sku": product.sku,
        "external_id": product.external_id,
        "product_url": product.product_url,
        "price_min": product.price_min,
        "price_max": product.price_max,
        "image_url": image_urls[0] if image_urls else None,
    }


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
