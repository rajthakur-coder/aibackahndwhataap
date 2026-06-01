import json
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import (
    EcommerceConnection,
    EcommerceOrder,
    EcommerceProduct,
    ShopifyCatalogCollection,
    ShopifyCatalogDefaultCategory,
)
from app.models.whatsapp import WhatsappCredential, WhatsappTemplate
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingRequest
from app.modules.ecommerce.bundles.bundle_service import upsert_bundle_pairing
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.modules.knowledge.knowledge_service import save_knowledge_base
from app.modules.scraper.scraper_schema import ScraperInput
from app.modules.scraper.scraper_service import run_brand_scraper
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config, upsert_tenant_config
from app.shared.tenant import normalize_tenant_id


ONBOARDING_STEPS = [
    {
        "key": "connect_whatsapp",
        "title": "Connect WhatsApp",
        "description": "Connect an active WhatsApp Business credential.",
        "required": True,
        "target_minutes": 30,
    },
    {
        "key": "connect_oms",
        "title": "Connect OMS",
        "description": "Connect Shopify, WooCommerce, or Custom REST.",
        "required": True,
        "target_minutes": 5,
    },
    {
        "key": "import_catalog",
        "title": "Verify catalog access",
        "description": "Verify Shopify catalog access and refresh the product cache when needed.",
        "required": True,
        "target_minutes": 30,
    },
    {
        "key": "brand_voice",
        "title": "Brand voice",
        "description": "Generate or confirm the tenant brand prompt.",
        "required": True,
        "target_minutes": 10,
    },
    {
        "key": "faq",
        "title": "FAQ knowledge base",
        "description": "Upload or generate FAQ and policy context.",
        "required": True,
        "target_minutes": 20,
    },
    {
        "key": "policies",
        "title": "Policies",
        "description": "Configure return, shipping, warranty, and COD policies.",
        "required": True,
        "target_minutes": 15,
    },
    {
        "key": "discounts",
        "title": "Discounts",
        "description": "Configure discount rules.",
        "required": False,
        "target_minutes": 10,
    },
    {
        "key": "bundle_pairs",
        "title": "Bundle pairs",
        "description": "Configure top product pairings.",
        "required": False,
        "target_minutes": 20,
    },
    {
        "key": "preview_test",
        "title": "Preview and test",
        "description": "Run a sandbox test conversation.",
        "required": True,
        "target_minutes": 15,
    },
    {
        "key": "go_live",
        "title": "Go live",
        "description": "Confirm templates, verification, and live readiness.",
        "required": True,
        "target_minutes": 10,
    },
]


async def ai_assist_from_website(db, tenant_id: str, website_url: str, apply: bool = True) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    scrape = await run_brand_scraper(ScraperInput(website_link=website_url))
    data = scrape.data.model_dump() if hasattr(scrape.data, "model_dump") else dict(scrape.data)
    brand_name = data.get("company_name") or _domain_brand_name(website_url)
    brand_voice_prompt = _brand_voice_prompt(data, brand_name)
    faqs = _draft_faqs(data, brand_name)
    policies = _draft_policies(data)
    tenant_payload = {
        "brand_name": brand_name,
        "brand_voice_prompt": brand_voice_prompt,
        "categories": _suggest_categories(data),
        "metadata": {
            "website_url": website_url,
            "logo": data.get("logo"),
            "fonts": data.get("fonts") or [],
            "color_palette": data.get("color_palette") or [],
            "socials": data.get("socials") or [],
            "store_domain": _domain(website_url),
            "onboarding": {
                "brand_voice": True,
                "faq": True,
                "policies": bool(policies),
            },
        },
    }
    if apply:
        await db.run_sync(lambda sync_db: upsert_tenant_config(sync_db, tenant_payload, tenant_id=tenant_id))
        await db.run_sync(
            lambda sync_db: save_knowledge_base(
                sync_db,
                KnowledgeBaseRequest(
                    website_link=website_url,
                    company_name=brand_name,
                    industry=data.get("industry"),
                    about_company=data.get("about_company"),
                    target_demographics=data.get("target_demographics"),
                    logo=data.get("logo"),
                    socials=data.get("socials") or [],
                    page_images=data.get("page_images") or [],
                    policies=policies,
                    faqs=faqs,
                ),
                tenant_id=tenant_id,
            )
        )
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "applied": apply,
        "tenant_config": tenant_payload,
        "knowledge_base": {
            "website_link": website_url,
            "company_name": brand_name,
            "policies": policies,
            "faqs": faqs,
        },
        "scrape": data,
    }


def onboarding_status(db: Session, tenant_id: str) -> dict:
    wizard = onboarding_wizard(db, tenant_id)
    return {
        "tenant_id": wizard["tenant_id"],
        **{step["key"]: step["completed"] for step in wizard["steps"]},
    }


def onboarding_wizard(db: Session, tenant_id: str) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    config = get_tenant_config(db, tenant_id)
    data = serialize_tenant_config(config) if config else {"metadata": {}}
    metadata = data.get("metadata") or {}
    onboarding = metadata.get("onboarding") or {}
    detected = _detected_step_status(db, tenant_id, data, onboarding)
    steps = []
    for index, definition in enumerate(ONBOARDING_STEPS, start=1):
        key = definition["key"]
        completed = bool(detected.get(key) or onboarding.get(key) == "completed" or onboarding.get(key) is True)
        blocked_by = _blocked_by(steps) if definition["required"] else []
        steps.append(
            {
                **definition,
                "order": index,
                "completed": completed,
                "status": "completed" if completed else ("blocked" if blocked_by else "pending"),
                "blocked_by": blocked_by,
                "action": _step_action(key),
            }
        )
    required = [step for step in steps if step["required"]]
    completed_required = [step for step in required if step["completed"]]
    return {
        "tenant_id": tenant_id,
        "status": "ready_for_live" if len(completed_required) == len(required) else "in_progress",
        "completion_percent": round((len([step for step in steps if step["completed"]]) / len(steps)) * 100),
        "required_completion_percent": round((len(completed_required) / len(required)) * 100),
        "estimated_total_minutes": sum(step["target_minutes"] for step in steps),
        "estimated_remaining_minutes": sum(step["target_minutes"] for step in steps if not step["completed"]),
        "next_step": next((step for step in steps if step["status"] == "pending"), None),
        "next_required_step": next((step for step in steps if step["required"] and step["status"] == "pending"), None),
        "steps": steps,
    }


def update_onboarding_step(db: Session, tenant_id: str, step_key: str, status: str = "completed", data: dict | None = None) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    if step_key not in {step["key"] for step in ONBOARDING_STEPS}:
        raise ValueError("Unknown onboarding step")
    config = get_tenant_config(db, tenant_id)
    existing = serialize_tenant_config(config) if config else {"brand_name": tenant_id, "metadata": {}}
    metadata = existing.get("metadata") or {}
    onboarding = metadata.get("onboarding") if isinstance(metadata.get("onboarding"), dict) else {}
    onboarding[step_key] = status
    if data:
        onboarding[f"{step_key}_data"] = data
    metadata["onboarding"] = onboarding
    upsert_tenant_config(db, {"brand_name": existing.get("brand_name") or tenant_id, "metadata": metadata}, tenant_id=tenant_id)
    return onboarding_wizard(db, tenant_id)


def preview_onboarding_bot(db: Session, tenant_id: str, message: str, phone: str | None = None, channel: str = "sandbox") -> dict:
    from app.modules.ai.orchestrator.orchestrator_service import orchestrate_message

    tenant_id = normalize_tenant_id(tenant_id)
    response = orchestrate_message(
        db,
        phone=phone or f"sandbox:{tenant_id}",
        message=message,
        tenant_id=tenant_id,
    )
    wizard = update_onboarding_step(
        db,
        tenant_id,
        "preview_test",
        "completed",
        {"channel": channel, "message": message, "selected_tool": response.selected_tool},
    )
    return {
        "status": "success",
        "tenant_id": tenant_id,
        "reply": response.reply,
        "selected_tool": response.selected_tool,
        "intent": response.intent,
        "wizard": wizard,
    }


def go_live_readiness(db: Session, tenant_id: str) -> dict:
    wizard = onboarding_wizard(db, tenant_id)
    blockers = [
        {"step": step["key"], "title": step["title"]}
        for step in wizard["steps"]
        if step["required"] and not step["completed"] and step["key"] != "go_live"
    ]
    templates_ready = _templates_ready(db, normalize_tenant_id(tenant_id))
    if not templates_ready:
        blockers.append({"step": "templates", "title": "WhatsApp templates approved or submitted"})
    return {
        "tenant_id": normalize_tenant_id(tenant_id),
        "ready": not blockers,
        "blockers": blockers,
        "wizard": wizard,
    }


def mark_go_live(db: Session, tenant_id: str) -> dict:
    readiness = go_live_readiness(db, tenant_id)
    if not readiness["ready"]:
        raise ValueError("Tenant is not ready to go live")
    return update_onboarding_step(db, tenant_id, "go_live", "completed", {"confirmed": True})


def suggest_bundle_pairings(db: Session, tenant_id: str, limit: int = 20) -> list[dict]:
    tenant_id = normalize_tenant_id(tenant_id)
    limit = max(1, min(int(limit or 20), 50))
    products_by_key = _product_lookup(db, tenant_id)
    pair_counts = defaultdict(int)
    orders = db.execute(
        select(EcommerceOrder)
        .where(EcommerceOrder.tenant_id == tenant_id)
        .order_by(EcommerceOrder.updated_at.desc())
        .limit(1000)
    ).scalars().all()
    for order in orders:
        keys = _order_skus(order)
        for primary in keys:
            for paired in keys:
                if primary != paired:
                    pair_counts[(primary, paired)] += 1
    suggestions = []
    for (primary, paired), count in sorted(pair_counts.items(), key=lambda item: item[1], reverse=True):
        if primary not in products_by_key or paired not in products_by_key:
            continue
        existing = next((item for item in suggestions if item["primary_sku"] == primary), None)
        if existing:
            if paired not in existing["paired_skus"]:
                existing["paired_skus"].append(paired)
                existing["confidence"] += count
            continue
        suggestions.append(
            {
                "primary_sku": primary,
                "primary_title": products_by_key[primary].title,
                "paired_skus": [paired],
                "paired_titles": [products_by_key[paired].title],
                "confidence": count,
                "reason": "Frequently bought together in cached order history.",
            }
        )
        if len(suggestions) >= limit:
            break
    if suggestions:
        return suggestions
    products = list(products_by_key.values())[: limit * 2]
    for product in products[:limit]:
        paired = [candidate for candidate in products if candidate.sku != product.sku and candidate.product_type == product.product_type][:3]
        if paired:
            suggestions.append(
                {
                    "primary_sku": product.sku,
                    "primary_title": product.title,
                    "paired_skus": [item.sku for item in paired if item.sku],
                    "paired_titles": [item.title for item in paired],
                    "confidence": 1,
                    "reason": "Same category/catalog type fallback.",
                }
            )
    return suggestions[:limit]


def apply_bundle_suggestions(db: Session, tenant_id: str, suggestions: list[dict], discount_type: str | None = None, discount_value: str | None = None) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    saved = []
    for item in suggestions:
        primary = str(item.get("primary_sku") or "").strip()
        paired = [str(sku).strip() for sku in item.get("paired_skus") or [] if str(sku).strip()]
        if not primary or not paired:
            continue
        saved.append(
            upsert_bundle_pairing(
                db,
                BundlePairingRequest(
                    primary_sku=primary,
                    paired_skus=paired,
                    discount_type=discount_type,
                    discount_value=discount_value,
                    status="active",
                    notes=item.get("reason"),
                ),
                tenant_id=tenant_id,
            )
        )
    return {"status": "success", "tenant_id": tenant_id, "saved": saved}


def _detected_step_status(db: Session, tenant_id: str, data: dict, onboarding: dict) -> dict:
    return {
        "connect_whatsapp": _has_active_whatsapp(db, tenant_id),
        "connect_oms": _has_active_oms(db, tenant_id),
        "import_catalog": _has_catalog(db, tenant_id),
        "brand_voice": bool(data.get("brand_voice_prompt") or onboarding.get("brand_voice")),
        "faq": _has_knowledge(db, tenant_id) or bool(onboarding.get("faq")),
        "policies": bool(data.get("return_policy") or data.get("shipping_policy") or data.get("warranty_policy") or onboarding.get("policies")),
        "discounts": bool(data.get("discount_rules") or onboarding.get("discounts")),
        "bundle_pairs": _has_bundles(db, tenant_id),
        "preview_test": bool(onboarding.get("preview_test")),
        "go_live": bool(onboarding.get("go_live")),
    }


def _blocked_by(existing_steps: list[dict]) -> list[str]:
    return [step["key"] for step in existing_steps if step["required"] and not step["completed"]]


def _step_action(step_key: str) -> dict:
    return {
        "connect_whatsapp": {"method": "POST", "path": "/whatsapp-credential/number-setup"},
        "connect_oms": {"method": "POST", "path": "/ecommerce/connections"},
        "import_catalog": {"method": "POST", "path": "/ecommerce/sync-active"},
        "brand_voice": {"method": "POST", "path": "/onboarding/ai-assist/from-website"},
        "faq": {"method": "POST", "path": "/knowledge"},
        "policies": {"method": "PUT", "path": "/tenants/current/config"},
        "discounts": {"method": "PUT", "path": "/tenants/current/config"},
        "bundle_pairs": {"method": "POST", "path": "/onboarding/suggest-bundles"},
        "preview_test": {"method": "POST", "path": "/onboarding/preview-test"},
        "go_live": {"method": "POST", "path": "/onboarding/go-live"},
    }.get(step_key, {})


def _has_active_whatsapp(db: Session, tenant_id: str) -> bool:
    return bool(
        db.execute(
            select(WhatsappCredential.id)
            .where(
                WhatsappCredential.tenant_id == tenant_id,
                WhatsappCredential.status == "active",
                WhatsappCredential.phone_number_id.is_not(None),
            )
            .limit(1)
        ).scalar()
    )


def _has_active_oms(db: Session, tenant_id: str) -> bool:
    return bool(
        db.execute(
            select(EcommerceConnection.id)
            .where(
                EcommerceConnection.tenant_id == tenant_id,
                EcommerceConnection.status == "active",
            )
            .limit(1)
        ).scalar()
    )


def _templates_ready(db: Session, tenant_id: str) -> bool:
    rows = db.execute(select(WhatsappTemplate.status).where(WhatsappTemplate.tenant_id == tenant_id).limit(20)).scalars().all()
    if not rows:
        return True
    return any(str(status or "").upper() in {"APPROVED", "ACTIVE"} for status in rows)


def _domain(value: str) -> str:
    return re.sub(r"^https?://", "", str(value or "").strip()).split("/", 1)[0]


def _domain_brand_name(value: str) -> str:
    host = _domain(value).removeprefix("www.")
    return " ".join(part.capitalize() for part in host.split(".")[0].replace("-", " ").split()) or "Commerce Brand"


def _brand_voice_prompt(data: dict, brand_name: str) -> str:
    about = str(data.get("about_company") or "").strip()
    industry = str(data.get("industry") or "commerce").strip()
    return (
        f"You are the WhatsApp commerce assistant for {brand_name}, a {industry} brand. "
        "Be concise, warm, helpful, and factual. Use the catalog, FAQ, policy, and order tools for facts. "
        "Do not invent prices, delivery dates, warranty terms, discounts, or availability. "
        f"Brand context: {about[:500]}"
    ).strip()


def _draft_faqs(data: dict, brand_name: str) -> str:
    about = str(data.get("about_company") or "").strip()
    return "\n".join(
        [
            f"Q: What is {brand_name}?",
            f"A: {about or brand_name + ' is a D2C commerce brand.'}",
            "Q: How can I track my order?",
            "A: Share your order ID or the phone number used for the order.",
            "Q: Can I get product recommendations?",
            "A: Yes. Share what you are looking for and the assistant will suggest catalog items.",
        ]
    )


def _draft_policies(data: dict) -> str:
    return (
        "Return, shipping, warranty, COD, and cancellation policies should be confirmed by the brand. "
        "Until configured, use live order/courier data and avoid promising timelines or eligibility."
    )


def _suggest_categories(data: dict) -> list[str]:
    industry = str(data.get("industry") or "").strip()
    categories = ["Best Sellers", "New Arrivals", "Gifting"]
    if industry:
        categories.insert(0, industry)
    return categories[:6]


def _has_catalog(db: Session, tenant_id: str) -> bool:
    if db.execute(select(EcommerceProduct.id).where(EcommerceProduct.tenant_id == tenant_id).limit(1)).scalar():
        return True

    active_shopify_connection_ids = (
        select(EcommerceConnection.id)
        .where(
            EcommerceConnection.tenant_id == tenant_id,
            EcommerceConnection.platform == "shopify",
            EcommerceConnection.status == "active",
        )
        .subquery()
    )
    has_visible_collection = db.execute(
        select(ShopifyCatalogCollection.id)
        .where(
            ShopifyCatalogCollection.tenant_id == tenant_id,
            ShopifyCatalogCollection.connection_id.in_(select(active_shopify_connection_ids.c.id)),
            ShopifyCatalogCollection.visible.in_(("true", "1", "yes", "on")),
        )
        .limit(1)
    ).scalar()
    if has_visible_collection:
        return True

    return bool(
        db.execute(
            select(ShopifyCatalogDefaultCategory.id)
            .where(
                ShopifyCatalogDefaultCategory.tenant_id == tenant_id,
                ShopifyCatalogDefaultCategory.connection_id.in_(select(active_shopify_connection_ids.c.id)),
                ShopifyCatalogDefaultCategory.visible.in_(("true", "1", "yes", "on")),
            )
            .limit(1)
        ).scalar()
    )


def _has_bundles(db: Session, tenant_id: str) -> bool:
    from app.models.ecommerce import EcommerceBundlePairing

    return bool(db.execute(select(EcommerceBundlePairing.id).where(EcommerceBundlePairing.tenant_id == tenant_id).limit(1)).scalar())


def _has_knowledge(db: Session, tenant_id: str) -> bool:
    from app.models.knowledge import KnowledgeBase

    return bool(db.execute(select(KnowledgeBase.id).where(KnowledgeBase.tenant_id == tenant_id).limit(1)).scalar())


def _product_lookup(db: Session, tenant_id: str) -> dict[str, EcommerceProduct]:
    rows = db.execute(select(EcommerceProduct).where(EcommerceProduct.tenant_id == tenant_id).limit(2000)).scalars().all()
    return {str(row.sku or row.external_id or row.id): row for row in rows if row.sku or row.external_id}


def _order_skus(order: EcommerceOrder) -> list[str]:
    try:
        items = json.loads(order.items or "[]")
    except json.JSONDecodeError:
        items = []
    keys = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("sku") or item.get("product_id") or item.get("variant_id") or item.get("title") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys
