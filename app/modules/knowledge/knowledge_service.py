import json

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def _json_loads(value: str | None, fallback):
    if not value:
        return fallback
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return fallback
    return data


def _json_dumps(value) -> str:
    return json.dumps(value or [], ensure_ascii=True)


def get_or_create_knowledge_base(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> KnowledgeBase:
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(
        select(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id)
    ).scalars().first()
    if row:
        return row
    row = KnowledgeBase(tenant_id=tenant_id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def serialize_knowledge_base(row: KnowledgeBase) -> dict:
    return {
        "website_link": row.website_link,
        "company_name": row.company_name,
        "industry": row.industry,
        "about_company": row.about_company,
        "target_demographics": row.target_demographics,
        "logo": row.logo,
        "socials": _json_loads(row.socials, []),
        "page_images": _json_loads(row.page_images, []),
        "policies": row.policies,
        "faqs": row.faqs,
        "updated_at": str(row.updated_at) if row.updated_at else None,
    }


def save_knowledge_base(db: Session, data: KnowledgeBaseRequest, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    row = get_or_create_knowledge_base(db, tenant_id)
    row.website_link = (data.website_link or "").strip() or None
    row.company_name = (data.company_name or "").strip() or None
    row.industry = (data.industry or "").strip() or None
    row.about_company = (data.about_company or "").strip() or None
    row.target_demographics = (data.target_demographics or "").strip() or None
    row.logo = (data.logo or "").strip() or None
    row.socials = _json_dumps(data.socials)
    row.page_images = _json_dumps(data.page_images)
    row.policies = (data.policies or "").strip() or None
    row.faqs = (data.faqs or "").strip() or None
    db.commit()
    db.refresh(row)
    return serialize_knowledge_base(row)


def knowledge_context(db: Session, message: str = "", tenant_id: str = DEFAULT_TENANT_ID) -> str:
    tenant_id = normalize_tenant_id(tenant_id)
    try:
        row = db.execute(
            select(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id)
        ).scalars().first()
    except SQLAlchemyError:
        db.rollback()
        return ""
    if not row:
        return ""

    message_lower = (message or "").lower()
    parts = []
    if row.company_name or row.industry:
        parts.append(
            "Business: "
            + ", ".join(filter(None, [row.company_name, row.industry, row.website_link]))
        )
    if row.about_company:
        parts.append(f"About company: {row.about_company}")
    if row.target_demographics:
        parts.append(f"Target customers: {row.target_demographics}")
    if row.policies and any(
        term in message_lower
        for term in ("policy", "return", "refund", "shipping", "delivery", "cancel", "exchange")
    ):
        parts.append(f"Policies: {row.policies}")
    if row.faqs and any(term in message_lower for term in ("faq", "how", "what", "when", "where", "why", "can")):
        parts.append(f"FAQs: {row.faqs}")

    if row.policies and "Policies:" not in "\n".join(parts):
        parts.append(f"Policies: {row.policies[:1200]}")
    if row.faqs and "FAQs:" not in "\n".join(parts):
        parts.append(f"FAQs: {row.faqs[:1200]}")

    return "\n".join(parts)[:5000]
