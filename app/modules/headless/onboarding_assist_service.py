from __future__ import annotations

import csv
import io
import re
from typing import Any

from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ecommerce import EcommerceProduct
from app.modules.ecommerce.bundles.bundle_schema import BundlePairingRequest
from app.modules.ecommerce.bundles.bundle_service import upsert_bundle_pairing
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.modules.knowledge.knowledge_service import save_knowledge_base
from app.modules.scraper.scraper_schema import ScraperInput, ScraperResultOut
from app.modules.scraper.scraper_service import run_brand_scraper
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config, upsert_tenant_config
from app.shared.tenant import normalize_tenant_id


class WebsiteAssistRequest(BaseModel):
    website_url: HttpUrl
    save: bool = True


class FAQAssistRequest(BaseModel):
    content: str = Field(min_length=1)
    save: bool = True


class BundleSuggestRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=50)
    pairs_per_product: int = Field(default=3, ge=1, le=5)


class BundleApplyRequest(BaseModel):
    suggestions: list[dict[str, Any]] = Field(default_factory=list)


async def build_website_onboarding_assist(data: WebsiteAssistRequest) -> tuple[ScraperResultOut, str]:
    scrape = await run_brand_scraper(ScraperInput(website_link=data.website_url))
    return scrape.data, scrape.status


def save_website_onboarding_assist(
    db: Session,
    tenant_id: str,
    data: WebsiteAssistRequest,
    scraped: ScraperResultOut,
    scrape_status: str,
) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    existing = get_tenant_config(db, tenant_id)
    existing_data = serialize_tenant_config(existing) if existing else {"metadata": {}, "brand_name": tenant_id}

    brand_name = scraped.company_name or existing_data.get("brand_name") or tenant_id
    brand_voice_prompt = build_brand_voice_prompt(scraped, brand_name)
    faqs = draft_faq_text(scraped, brand_name)
    policies = draft_policy_text(scraped)
    metadata = existing_data.get("metadata") or {}
    onboarding = metadata.get("onboarding") or {}
    onboarding.update(
        {
            "brand_voice": True,
            "faq": True,
            "website_scrape": True,
            "ai_assisted_defaults": True,
        }
    )
    metadata.update(
        {
            "onboarding": onboarding,
            "website_assist": {
                "source_url": str(data.website_url),
                "status": scrape_status,
                "suggested_pairings_ready": False,
            },
        }
    )

    config_payload = {
        "brand_name": brand_name,
        "brand_voice_prompt": brand_voice_prompt,
        "default_tone": _tone_from_scrape(scraped),
        "categories": _categories_from_scrape(scraped),
        "metadata": metadata,
    }
    knowledge_payload = KnowledgeBaseRequest(
        website_link=str(data.website_url),
        company_name=brand_name,
        industry=scraped.industry,
        about_company=scraped.about_company,
        target_demographics=scraped.target_demographics,
        logo=scraped.logo,
        socials=[item.model_dump() for item in scraped.socials],
        page_images=scraped.page_images,
        policies=policies,
        faqs=faqs,
    )

    response = {
        "scrape": scraped.model_dump(),
        "drafts": {
            "brand_name": brand_name,
            "brand_voice_prompt": brand_voice_prompt,
            "faqs": faqs,
            "policies": policies,
            "categories": config_payload["categories"],
        },
        "saved": False,
    }
    if data.save:
        response["tenant_config"] = serialize_tenant_config(upsert_tenant_config(db, config_payload, tenant_id))
        response["knowledge_base"] = save_knowledge_base(db, knowledge_payload, tenant_id)
        response["saved"] = True
    return response


def faq_onboarding_assist(db: Session, tenant_id: str, data: FAQAssistRequest) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    extracted = extract_faq_pairs(data.content)
    faqs = "\n".join(f"Q: {item['question']}\nA: {item['answer']}" for item in extracted)
    response = {"faqs": extracted, "faq_text": faqs, "saved": False}
    if data.save:
        existing = get_tenant_config(db, tenant_id)
        metadata = (serialize_tenant_config(existing).get("metadata") if existing else {}) or {}
        onboarding = metadata.get("onboarding") or {}
        onboarding.update({"faq": bool(extracted), "ai_assisted_defaults": True})
        metadata["onboarding"] = onboarding
        upsert_tenant_config(db, {"brand_name": (existing.brand_name if existing else tenant_id), "metadata": metadata}, tenant_id)
        response["knowledge_base"] = save_knowledge_base(db, KnowledgeBaseRequest(faqs=faqs), tenant_id)
        response["saved"] = True
    return response


def suggest_bundle_pairings(db: Session, tenant_id: str, data: BundleSuggestRequest) -> list[dict[str, Any]]:
    tenant_id = normalize_tenant_id(tenant_id)
    products = db.execute(
        select(EcommerceProduct)
        .where(EcommerceProduct.tenant_id == tenant_id, EcommerceProduct.sku.is_not(None))
        .order_by(EcommerceProduct.updated_at.desc())
        .limit(250)
    ).scalars().all()
    suggestions: list[dict[str, Any]] = []
    for primary in products:
        scored = [
            (_bundle_score(primary, candidate), candidate)
            for candidate in products
            if candidate.id != primary.id and candidate.sku
        ]
        scored = [(score, candidate) for score, candidate in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        paired = [candidate.sku for _, candidate in scored[: data.pairs_per_product]]
        if not paired or not primary.sku:
            continue
        suggestions.append(
            {
                "primary_sku": primary.sku,
                "primary_title": primary.title,
                "paired_skus": paired,
                "discount_type": "percentage",
                "discount_value": "5",
                "reason": _bundle_reason(primary, [candidate for _, candidate in scored[: data.pairs_per_product]]),
                "confidence": min(0.95, round(scored[0][0] / 10, 2)),
            }
        )
        if len(suggestions) >= data.limit:
            break
    return suggestions


def apply_bundle_suggestions(db: Session, tenant_id: str, data: BundleApplyRequest) -> list[dict]:
    applied = []
    for suggestion in data.suggestions:
        primary_sku = str(suggestion.get("primary_sku") or "").strip()
        paired_skus = [str(sku).strip() for sku in suggestion.get("paired_skus") or [] if str(sku).strip()]
        if not primary_sku or not paired_skus:
            continue
        applied.append(
            upsert_bundle_pairing(
                db,
                BundlePairingRequest(
                    primary_sku=primary_sku,
                    paired_skus=paired_skus,
                    discount_type=suggestion.get("discount_type") or "percentage",
                    discount_value=str(suggestion.get("discount_value") or "5"),
                    notes=suggestion.get("reason") or "AI-assisted onboarding suggestion",
                ),
                tenant_id=tenant_id,
            )
        )
    existing = get_tenant_config(db, tenant_id)
    metadata = (serialize_tenant_config(existing).get("metadata") if existing else {}) or {}
    onboarding = metadata.get("onboarding") or {}
    onboarding["bundle_pairs"] = bool(applied)
    metadata["onboarding"] = onboarding
    upsert_tenant_config(db, {"brand_name": (existing.brand_name if existing else tenant_id), "metadata": metadata}, tenant_id)
    return applied


def build_brand_voice_prompt(scraped: ScraperResultOut, brand_name: str) -> str:
    industry = scraped.industry or "D2C commerce"
    audience = scraped.target_demographics or "the brand's shoppers"
    about = scraped.about_company or f"{brand_name} products"
    return (
        f"You are the WhatsApp commerce assistant for {brand_name}. "
        f"The brand operates in {industry} and serves {audience}. "
        f"Context: {about[:600]}. "
        "Reply warmly and concisely, match the customer's language, and keep responses WhatsApp-friendly. "
        "Do not invent prices, stock, delivery dates, return windows, or warranty details. "
        "Use catalog, OMS, policy, FAQ, checkout, and ticket tools whenever facts or actions are needed."
    )


def draft_faq_text(scraped: ScraperResultOut, brand_name: str) -> str:
    industry = scraped.industry or "products"
    return "\n".join(
        [
            f"Q: What does {brand_name} sell?",
            f"A: {scraped.about_company or f'{brand_name} sells {industry}. Use catalog tools for exact products, prices, and availability.'}",
            "Q: Can I get product recommendations on WhatsApp?",
            "A: Yes. Ask for the use case, budget, preferred style, size, or occasion, then use catalog search and bundle tools before recommending.",
            "Q: How can I check my order status?",
            "A: Ask for the order ID or registered phone number and use the OMS order status tool.",
            "Q: How do returns or exchanges work?",
            "A: Use the configured return policy and return tool. Do not promise eligibility unless policy and order data confirm it.",
        ]
    )


def draft_policy_text(scraped: ScraperResultOut) -> str:
    return (
        "Shipping, returns, warranty, COD, and cancellation rules must come from tenant policy settings or OMS/courier tools. "
        f"Website source: {scraped.website_link or 'not provided'}. "
        "If a customer asks for a policy that is not configured, create a support ticket instead of guessing."
    )


def extract_faq_pairs(content: str) -> list[dict[str, str]]:
    rows = _extract_csv_faqs(content)
    if rows:
        return rows
    pairs = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        question_match = re.search(r"(?:^|\n)\s*(?:q(?:uestion)?[:.-]\s*)?(.*\?)", block, flags=re.IGNORECASE)
        answer_match = re.search(r"(?:^|\n)\s*(?:a(?:nswer)?[:.-]\s*)(.+)", block, flags=re.IGNORECASE | re.DOTALL)
        if question_match and answer_match:
            pairs.append({"question": question_match.group(1).strip(), "answer": answer_match.group(1).strip()})
    if pairs:
        return pairs
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return [{"question": line, "answer": "Needs brand review."} for line in lines if line.endswith("?")]


def _extract_csv_faqs(content: str) -> list[dict[str, str]]:
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
    except csv.Error:
        return []
    output = []
    for row in rows:
        question = (row.get("question") or row.get("Question") or row.get("q") or "").strip()
        answer = (row.get("answer") or row.get("Answer") or row.get("a") or "").strip()
        if question and answer:
            output.append({"question": question, "answer": answer})
    return output


def _tone_from_scrape(scraped: ScraperResultOut) -> str:
    industry = (scraped.industry or "").lower()
    if any(term in industry for term in ("luxury", "decor", "home", "fashion", "beauty")):
        return "warm, polished, concise"
    if any(term in industry for term in ("health", "wellness", "baby")):
        return "calm, reassuring, concise"
    return "helpful, warm, concise"


def _categories_from_scrape(scraped: ScraperResultOut) -> list[str]:
    categories = ["Best Sellers", "New Arrivals", "Gifting"]
    if scraped.industry:
        categories.insert(0, scraped.industry)
    return list(dict.fromkeys(categories))[:6]


def _bundle_score(primary: EcommerceProduct, candidate: EcommerceProduct) -> int:
    score = 0
    primary_tags = _token_set(primary.tags, primary.collections, primary.product_type)
    candidate_tags = _token_set(candidate.tags, candidate.collections, candidate.product_type)
    shared = primary_tags.intersection(candidate_tags)
    score += min(4, len(shared))
    if primary.product_type and candidate.product_type and primary.product_type != candidate.product_type:
        score += 3
    if _price_bucket(primary.price_min) == _price_bucket(candidate.price_min):
        score += 2
    if primary.vendor and candidate.vendor and primary.vendor == candidate.vendor:
        score += 1
    return score


def _bundle_reason(primary: EcommerceProduct, paired: list[EcommerceProduct]) -> str:
    types = ", ".join(sorted({item.product_type for item in paired if item.product_type}))
    if types:
        return f"Complements {primary.product_type or primary.title} with related {types} products."
    return "Products share catalog signals such as collection, tags, vendor, or price band."


def _token_set(*values: str | None) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(re.findall(r"[a-z0-9]+", str(value or "").lower()))
    return {token for token in tokens if len(token) > 2}


def _price_bucket(value: str | None) -> int:
    try:
        price = float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0
    if price <= 0:
        return 0
    return int(price // 1000)
