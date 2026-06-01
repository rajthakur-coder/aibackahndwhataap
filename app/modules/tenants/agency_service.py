import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenants import AgencyTenantAccess, TenantConfig
from app.shared.tenant import normalize_tenant_id


def list_agency_clients(db: Session, agency_tenant_id: str) -> list[dict]:
    agency_tenant_id = normalize_tenant_id(agency_tenant_id)
    rows = db.execute(
        select(AgencyTenantAccess)
        .where(AgencyTenantAccess.agency_tenant_id == agency_tenant_id)
        .order_by(AgencyTenantAccess.updated_at.desc())
    ).scalars().all()
    return [serialize_agency_access(row, _tenant_config(db, row.client_tenant_id)) for row in rows]


def upsert_agency_client(db: Session, agency_tenant_id: str, payload: dict) -> dict:
    agency_tenant_id = normalize_tenant_id(agency_tenant_id)
    client_tenant_id = normalize_tenant_id(payload.get("client_tenant_id"))
    if client_tenant_id == agency_tenant_id:
        raise ValueError("Agency tenant cannot be its own client tenant")

    row = db.execute(
        select(AgencyTenantAccess).where(
            AgencyTenantAccess.agency_tenant_id == agency_tenant_id,
            AgencyTenantAccess.client_tenant_id == client_tenant_id,
        )
    ).scalars().first()
    if not row:
        row = AgencyTenantAccess(tenant_id=agency_tenant_id, agency_tenant_id=agency_tenant_id, client_tenant_id=client_tenant_id)
        db.add(row)
    row.tenant_id = agency_tenant_id

    row.role = str(payload.get("role") or row.role or "reseller_admin")
    row.status = str(payload.get("status") or row.status or "active")
    row.white_label_name = payload.get("white_label_name", row.white_label_name)
    row.white_label_domain = payload.get("white_label_domain", row.white_label_domain)
    row.support_email = payload.get("support_email", row.support_email)
    if "settings" in payload:
        row.settings_json = json.dumps(payload.get("settings") or {}, ensure_ascii=True)
    db.commit()
    db.refresh(row)
    return serialize_agency_access(row, _tenant_config(db, row.client_tenant_id))


def agency_overview(db: Session, agency_tenant_id: str) -> dict:
    clients = list_agency_clients(db, agency_tenant_id)
    active = [row for row in clients if row.get("status") == "active"]
    return {
        "agency_tenant_id": normalize_tenant_id(agency_tenant_id),
        "client_count": len(clients),
        "active_client_count": len(active),
        "clients": clients,
    }


def white_label_profile(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    direct = db.execute(
        select(AgencyTenantAccess)
        .where(AgencyTenantAccess.client_tenant_id == tenant_id, AgencyTenantAccess.status == "active")
        .order_by(AgencyTenantAccess.updated_at.desc())
        .limit(1)
    ).scalars().first()
    if direct:
        return {
            "tenant_id": tenant_id,
            "agency_tenant_id": direct.agency_tenant_id,
            "white_label": {
                "name": direct.white_label_name,
                "domain": direct.white_label_domain,
                "support_email": direct.support_email,
                "settings": _loads(direct.settings_json),
            },
        }
    config = _tenant_config(db, tenant_id)
    metadata = _loads(getattr(config, "metadata_json", None))
    return {
        "tenant_id": tenant_id,
        "agency_tenant_id": None,
        "white_label": metadata.get("white_label") if isinstance(metadata.get("white_label"), dict) else {},
    }


def serialize_agency_access(row: AgencyTenantAccess, config: TenantConfig | None = None) -> dict:
    return {
        "id": row.id,
        "agency_tenant_id": row.agency_tenant_id,
        "client_tenant_id": row.client_tenant_id,
        "client_brand_name": getattr(config, "brand_name", None),
        "role": row.role,
        "status": row.status,
        "white_label_name": row.white_label_name,
        "white_label_domain": row.white_label_domain,
        "support_email": row.support_email,
        "settings": _loads(row.settings_json),
    }


def _tenant_config(db: Session, tenant_id: str) -> TenantConfig | None:
    return db.execute(select(TenantConfig).where(TenantConfig.tenant_id == normalize_tenant_id(tenant_id))).scalars().first()


def _loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
