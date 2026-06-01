import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tenants import TenantConfig
from app.modules.tenants.tenant_schema import TenantConfigRequest
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


TENANT_CONFIG_TEMPLATES = {
    "commerce": {
        "brand_name": "Commerce Brand",
        "brand_voice_prompt": (
            "You are the conversation layer for a D2C commerce brand. "
            "Be warm, calm, concise, and editorial. Use Hinglish only if the user uses it first. "
            "Never invent specs, prices, delivery dates, return windows, or warranty details. "
            "Use catalog, policy, and order tools when facts are needed. Keep WhatsApp replies short."
        ),
        "return_policy": "Returns and exchanges are available within 7 days from delivery, subject to category eligibility and item condition. Final sale items are not returnable.",
        "shipping_policy": "Use order and courier tools for live delivery status. Do not promise delivery dates unless the OMS or courier status provides an estimate.",
        "warranty_policy": "Use the configured FAQ or product data for warranty claims. Do not promise warranty coverage unless policy context confirms it.",
        "discount_rules": [
            {"code": "FIRST10", "type": "percentage", "value": 10, "cap": 500, "min_order": 1500, "trigger": "first_purchase"},
            {"code": "WELCOME15", "type": "percentage", "value": 15, "trigger": "post_return_goodwill", "valid_days": 60},
            {"code": "REVIEW100", "type": "fixed", "value": 100, "trigger": "post_review"},
            {"code": "WA_EXCLUSIVE", "type": "free_shipping", "min_order": 1500, "trigger": "whatsapp_checkout"},
        ],
        "categories": ["Best Sellers", "New Arrivals", "Gifting"],
        "support_email": "",
        "support_sla_hours": 4,
        "default_emoji": "",
        "default_tone": "warm, calm, concise",
        "metadata": {
            "template": "commerce",
            "bulk_lead_sla_business_days": 2,
        },
    }
}


def get_tenant_config(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> TenantConfig | None:
    tenant_id = normalize_tenant_id(tenant_id)
    return db.execute(select(TenantConfig).where(TenantConfig.tenant_id == tenant_id)).scalars().first()


def upsert_tenant_config(
    db: Session,
    data: TenantConfigRequest | dict,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> TenantConfig:
    tenant_id = normalize_tenant_id(tenant_id)
    payload = data.model_dump(exclude_unset=True) if isinstance(data, TenantConfigRequest) else dict(data)
    row = get_tenant_config(db, tenant_id)
    if not row:
        row = TenantConfig(tenant_id=tenant_id, brand_name=str(payload.get("brand_name") or tenant_id))
        db.add(row)

    for field in (
        "brand_name",
        "brand_voice_prompt",
        "return_policy",
        "shipping_policy",
        "warranty_policy",
        "support_email",
        "support_sla_hours",
        "default_emoji",
        "default_tone",
    ):
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])

    if "discount_rules" in payload:
        row.discount_rules = _json_dumps(payload.get("discount_rules") or [])
    if "categories" in payload:
        row.categories = _json_dumps(payload.get("categories") or [])
    if "metadata" in payload:
        row.metadata_json = _json_dumps(payload.get("metadata") or {})

    db.commit()
    db.refresh(row)
    return row


def seed_tenant_config(
    db: Session,
    tenant_id: str = DEFAULT_TENANT_ID,
    template: str = "commerce",
    overwrite: bool = False,
) -> TenantConfig:
    template_key = (template or "").strip().lower()
    payload = TENANT_CONFIG_TEMPLATES.get(template_key)
    if not payload:
        raise ValueError(f"Unknown tenant config template: {template}")

    existing = get_tenant_config(db, tenant_id)
    if existing and not overwrite:
        return existing
    return upsert_tenant_config(db, payload, tenant_id=tenant_id)


def serialize_tenant_config(row: TenantConfig) -> dict:
    return {
        "tenant_id": row.tenant_id,
        "brand_name": row.brand_name,
        "brand_voice_prompt": row.brand_voice_prompt,
        "return_policy": row.return_policy,
        "shipping_policy": row.shipping_policy,
        "warranty_policy": row.warranty_policy,
        "discount_rules": _json_loads(row.discount_rules, []),
        "categories": _json_loads(row.categories, []),
        "support_email": row.support_email,
        "support_sla_hours": row.support_sla_hours,
        "default_emoji": row.default_emoji,
        "default_tone": row.default_tone,
        "metadata": _json_loads(row.metadata_json, {}),
    }


def tenant_config_context(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> str:
    row = get_tenant_config(db, tenant_id)
    if not row:
        return ""

    discounts = _json_loads(row.discount_rules, [])
    categories = _json_loads(row.categories, [])
    lines = [
        f"Brand: {row.brand_name}",
        f"Brand voice: {row.brand_voice_prompt or ''}",
        f"Default tone: {row.default_tone or ''}",
        f"Default emoji: {row.default_emoji or ''}",
        f"Categories: {', '.join(categories)}",
        f"Return policy: {row.return_policy or ''}",
        f"Shipping policy: {row.shipping_policy or ''}",
        f"Warranty policy: {row.warranty_policy or ''}",
        f"Support email: {row.support_email or ''}",
        f"Support SLA hours: {row.support_sla_hours or ''}",
        "Discount rules: " + json.dumps(discounts, ensure_ascii=True, default=str),
    ]
    return "\n".join(line for line in lines if line.strip())


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback
