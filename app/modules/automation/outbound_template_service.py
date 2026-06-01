import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation import AutomationRule, MessageTemplate
from app.modules.compliance.template_compliance import check_template_compliance
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config
from app.modules.whatsapp.templates import template_service as meta_template_service
from app.shared.tenant import normalize_tenant_id


OUTBOUND_TEMPLATE_BLUEPRINTS = [
    {
        "key": "abandoned_cart_recovery",
        "trigger": "cart_abandoned",
        "category": "MARKETING",
        "body_variable_order": ["customer_name"],
        "button_variable_order": ["cart_token"],
        "delay_seconds": 86400,
        "body": "Hi {{customer_name}}, your {brand_name} cart is still saved. Tap below to complete your order.",
        "meta_body": "Hi {{1}}, your {brand_name} cart is still saved. Tap below to complete your order.",
        "cart_button": True,
    },
    {
        "key": "delivered_review",
        "trigger": "delivered_review",
        "category": "MARKETING",
        "body_variable_order": ["customer_name", "order_number"],
        "delay_seconds": 86400,
        "body": "Hope you are loving order {{order_number}}, {{customer_name}}. Reply with a rating from 1 to 5.",
        "meta_body": "Hope you are loving order {{2}}, {{1}}. Reply with a rating from 1 to 5.",
    },
    {
        "key": "replenishment",
        "trigger": "replenishment",
        "category": "MARKETING",
        "body_variable_order": ["customer_name", "product_name"],
        "delay_seconds": 7776000,
        "body": "Running low on {{product_name}}, {{customer_name}}? Reply YES to reorder or see refills.",
        "meta_body": "Running low on {{2}}, {{1}}? Reply YES to reorder or see refills.",
    },
    {
        "key": "browse_no_buy",
        "trigger": "browse_no_buy",
        "category": "MARKETING",
        "body_variable_order": ["customer_name"],
        "delay_seconds": 259200,
        "body": "Still thinking it over, {{customer_name}}? Reply YES and I will bring back the items you viewed.",
        "meta_body": "Still thinking it over, {{1}}? Reply YES and I will bring back the items you viewed.",
    },
    {
        "key": "post_dispatch_cross_sell",
        "trigger": "post_dispatch_cross_sell",
        "category": "MARKETING",
        "body_variable_order": ["customer_name", "product_name"],
        "delay_seconds": 86400,
        "body": "Your {{product_name}} is on the way, {{customer_name}}. Reply YES to see matching picks.",
        "meta_body": "Your {{2}} is on the way, {{1}}. Reply YES to see matching picks.",
    },
]


def seed_outbound_templates(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    tenant = _tenant_payload(db, tenant_id)
    templates = [_tenant_template(tenant_id, tenant, blueprint) for blueprint in OUTBOUND_TEMPLATE_BLUEPRINTS]
    templates_created = 0
    rules_created = 0
    rules_updated = 0
    for data in templates:
        template, created = _upsert_message_template(db, data, tenant_id)
        templates_created += int(created)
        created_rule, updated_rule = _upsert_automation_rule(db, data, template, tenant_id)
        rules_created += int(created_rule)
        rules_updated += int(updated_rule)
    db.commit()
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "templates": len(templates),
        "templates_created": templates_created,
        "rules_created": rules_created,
        "rules_updated": rules_updated,
    }


def submit_outbound_templates_to_meta(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    tenant = _tenant_payload(db, tenant_id)
    results = []
    for data in [_tenant_template(tenant_id, tenant, blueprint) for blueprint in OUTBOUND_TEMPLATE_BLUEPRINTS]:
        payload = {
            "name": data["name"],
            "language": "en",
            "category": data["category"],
            "parameter_format": "POSITIONAL",
            "components": data["components"],
        }
        compliance = check_template_compliance(payload)
        if not compliance["ok"]:
            results.append({"name": data["name"], "status": "blocked", "issues": compliance["issues"]})
            continue
        try:
            result = meta_template_service.register_template(db, payload, tenant_id=tenant_id)
        except Exception as exc:
            result = {"success": False, "message": str(exc)}
        results.append({"name": data["name"], "result": result})
    return {"status": "submitted", "tenant_id": tenant_id, "results": results}


def meta_template_approval_status(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    tenant = _tenant_payload(db, tenant_id)
    result = meta_template_service.list_templates(db, tenant_id=tenant_id, limit=100)
    rows = result.get("data") or []
    wanted = {template["name"] for template in [_tenant_template(tenant_id, tenant, blueprint) for blueprint in OUTBOUND_TEMPLATE_BLUEPRINTS]}
    statuses = [
        {"name": row.get("name"), "language": row.get("language"), "status": row.get("status")}
        for row in rows
        if row.get("name") in wanted
    ]
    approved = [row for row in statuses if str(row.get("status") or "").upper() == "APPROVED"]
    return {
        "tenant_id": tenant_id,
        "required": sorted(wanted),
        "found": statuses,
        "approved_count": len(approved),
        "ready": len(approved) == len(wanted),
    }


def _tenant_template(tenant_id: str, tenant: dict, blueprint: dict) -> dict:
    brand_name = _brand_name(tenant, tenant_id)
    name = f"{_template_prefix(tenant_id)}_{blueprint['key']}"
    body = blueprint["body"].format(brand_name=brand_name)
    components = [{"type": "BODY", "text": blueprint["meta_body"].format(brand_name=brand_name)}]
    cart_url = _cart_url_template(tenant)
    if blueprint.get("cart_button") and cart_url:
        components.append({"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Checkout", "url": cart_url}]})
    return {
        **blueprint,
        "name": name,
        "rule_name": f"{brand_name} {blueprint['trigger'].replace('_', ' ').title()}",
        "body": body,
        "components": components,
    }


def _tenant_payload(db: Session, tenant_id: str) -> dict:
    row = get_tenant_config(db, tenant_id)
    if not row:
        return {"tenant_id": normalize_tenant_id(tenant_id), "metadata": {}}
    return serialize_tenant_config(row)


def _brand_name(tenant: dict, tenant_id: str) -> str:
    return str(tenant.get("brand_name") or normalize_tenant_id(tenant_id)).strip()[:80]


def _template_prefix(tenant_id: str) -> str:
    normalized = normalize_tenant_id(tenant_id).lower()
    return re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")[:32] or "brand"


def _cart_url_template(tenant: dict) -> str | None:
    metadata = tenant.get("metadata") or {}
    explicit = str(metadata.get("cart_url_template") or "").strip()
    if explicit:
        return explicit
    domain = str(metadata.get("store_domain") or "").strip().removeprefix("https://").removeprefix("http://").strip("/")
    if not domain:
        return None
    return f"https://{domain}/cart/{{{{1}}}}"


def _upsert_message_template(db: Session, data: dict, tenant_id: str) -> tuple[MessageTemplate, bool]:
    row = db.execute(select(MessageTemplate).where(MessageTemplate.tenant_id == tenant_id, MessageTemplate.name == data["name"])).scalars().first()
    created = False
    if not row:
        row = MessageTemplate(tenant_id=tenant_id, name=data["name"], body=data["body"])
        db.add(row)
        created = True
    row.body = data["body"]
    row.channel = "whatsapp"
    row.template_type = "whatsapp_template"
    row.provider_template_name = data["name"]
    row.language = "en"
    row.body_variable_order = json.dumps(data.get("body_variable_order") or [], ensure_ascii=True)
    row.status = "active"
    db.flush()
    return row, created


def _upsert_automation_rule(db: Session, data: dict, template: MessageTemplate, tenant_id: str) -> tuple[bool, bool]:
    row = db.execute(select(AutomationRule).where(AutomationRule.tenant_id == tenant_id, AutomationRule.name == data["rule_name"])).scalars().first()
    created = False
    updated = False
    if not row:
        row = AutomationRule(tenant_id=tenant_id, name=data["rule_name"], trigger=data["trigger"])
        db.add(row)
        created = True
    for field, value in {
        "trigger": data["trigger"],
        "message_template_id": template.id,
        "message_body": None,
        "delay_seconds": data.get("delay_seconds") or 0,
        "enabled": "true",
        "conditions": json.dumps(data.get("conditions") or {}, ensure_ascii=True),
    }.items():
        if getattr(row, field) != value:
            setattr(row, field, value)
            updated = True
    return created, updated and not created
