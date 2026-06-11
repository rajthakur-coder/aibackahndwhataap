import json
import re

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase
from app.modules.knowledge.knowledge_schema import KnowledgeBaseRequest
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?[6-9]\d{9}\b")
CONTACT_QUERY_TERMS = (
    "contact",
    "email",
    "e-mail",
    "mail",
    "mobile",
    "phone",
    "number",
    "call",
    "whatsapp",
    "support",
)


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
        "contact_email": row.contact_email,
        "contact_phone": row.contact_phone,
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
    row.contact_email = _clean_email(data.contact_email) or _first_email(
        data.about_company,
        data.policies,
        data.faqs,
        data.website_link,
    )
    row.contact_phone = _clean_phone(data.contact_phone) or _first_phone(
        data.about_company,
        data.policies,
        data.faqs,
    )
    row.about_company = (data.about_company or "").strip() or None
    row.target_demographics = (data.target_demographics or "").strip() or None
    row.logo = (data.logo or "").strip() or None
    row.socials = _json_dumps(data.socials)
    row.page_images = _json_dumps(data.page_images)
    row.policies = _clean_knowledge_text(data.policies, kind="policies")
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
    contact_lines = _contact_context_lines(row)
    if row.company_name or row.industry:
        parts.append(
            "Business: "
            + ", ".join(filter(None, [row.company_name, row.industry, row.website_link]))
        )
    if contact_lines and any(term in message_lower for term in CONTACT_QUERY_TERMS):
        parts.append("Contact details: " + "; ".join(contact_lines))
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
    if contact_lines and "Contact details:" not in "\n".join(parts):
        parts.append("Contact details: " + "; ".join(contact_lines))

    return "\n".join(parts)[:5000]


def business_contact_details(db: Session, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    row = db.execute(
        select(KnowledgeBase).where(KnowledgeBase.tenant_id == tenant_id)
    ).scalars().first()
    if not row:
        return {"email": None, "phone": None}

    email = _clean_email(row.contact_email) or _first_email(
        row.about_company,
        row.policies,
        row.faqs,
        row.socials,
    )
    phone = _clean_phone(row.contact_phone) or _first_phone(
        row.about_company,
        row.policies,
        row.faqs,
        row.socials,
    )
    return {"email": email, "phone": phone}


def _clean_knowledge_text(text: str | None, *, kind: str = "text") -> str | None:
    text = _repair_common_mojibake(str(text or ""))
    noisy_terms = (
        "skip to content",
        "your cart is empty",
        "continue shopping",
        "have an account?",
        "log in",
        "your cart",
        "loading",
        "estimated total",
        "check out",
        "checkout",
        "taxes included",
        "prepaid orders",
        "extra 5% off",
        "payday",
        "sale is live",
        "opens in a new window",
        "is blocked",
        "err_blocked_by_client",
        "base64-image-removed",
        "top selling",
        "powered by shopify",
    )
    noisy_exact = {
        "home improvement",
        "kitchen",
        "organisers",
        "cooking",
        "furnishing",
        "log in",
        "cart",
        "company",
        "search",
        "about us",
        "contact us",
        "track your orders",
        "policy",
        "payment methods",
    }
    clean_lines = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if kind == "policies" and lowered in noisy_exact:
            continue
        if lowered.startswith("![") or lowered.startswith("[](") or lowered.startswith("[skip"):
            continue
        if re.fullmatch(r"(?:₹|rs\.?)\s*0(?:\.00)?", lowered):
            continue
        if kind == "policies" and re.fullmatch(r"\d+\s*(?:items?)?", lowered):
            continue
        if re.fullmatch(r"(?:₹|rs\.?)\s*0(?:\.00)?", lowered):
            continue
        if any(term in lowered for term in noisy_terms):
            continue
        clean_lines.append(candidate)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(clean_lines)).strip()
    if kind == "policies":
        cleaned = _prioritize_policy_sections(cleaned)
    return cleaned or None


def _clean_email(value: str | None) -> str | None:
    text = str(value or "").strip().strip("_*`.,;:()[]{}<>")
    match = EMAIL_RE.search(text)
    if not match:
        return None
    return match.group(0).strip("_*`.,;:()[]{}<>")


def _clean_phone(value: str | None) -> str | None:
    text = str(value or "").strip()
    match = PHONE_RE.search(text)
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _first_email(*values: object) -> str | None:
    for value in values:
        email = _clean_email(str(value or ""))
        if email:
            return email
    return None


def _first_phone(*values: object) -> str | None:
    for value in values:
        phone = _clean_phone(str(value or ""))
        if phone:
            return phone
    return None


def _contact_context_lines(row: KnowledgeBase) -> list[str]:
    details = business_contact_details_from_row(row)
    lines = []
    if details.get("email"):
        lines.append(f"support email={details['email']}")
    if details.get("phone"):
        lines.append(f"support phone={details['phone']}")
    return lines


def business_contact_details_from_row(row: KnowledgeBase) -> dict:
    return {
        "email": _clean_email(row.contact_email) or _first_email(row.about_company, row.policies, row.faqs, row.socials),
        "phone": _clean_phone(row.contact_phone) or _first_phone(row.about_company, row.policies, row.faqs, row.socials),
    }


def _prioritize_policy_sections(text: str) -> str:
    sections = _split_policy_sections(text)
    if not sections:
        return text[:12000]
    priority = (
        "return",
        "exchange",
        "refund",
        "shipping",
        "delivery",
        "cancel",
        "warranty",
        "cod",
    )
    picked = []
    for section in sections:
        lowered = section.lower()
        if any(term in lowered for term in priority):
            picked.append(section)
    picked = _dedupe_sections(sorted(picked or sections, key=_policy_section_rank))
    return "\n\n".join(picked)[:12000]


def _policy_section_rank(section: str) -> int:
    lowered = section.lower()
    if any(term in lowered for term in ("return", "exchange", "refund")):
        return 0
    if any(term in lowered for term in ("shipping", "delivery")):
        return 1
    if "cancel" in lowered:
        return 2
    if "warranty" in lowered:
        return 3
    if "cod" in lowered:
        return 4
    return 9


def _split_policy_sections(text: str) -> list[str]:
    raw_sections = re.split(
        r"(?=Policy source:\s*https?://|^#?\s*(?:Return & Exchange Policy|RETURN, EXCHANGE AND REFUND POLICY|Cancellation Policy|Shipping Policy|Reverse pickup & exchange timeline|Claiming Refunds|Privacy Policy|Terms & Conditions)\b)",
        text,
        flags=re.I | re.M,
    )
    sections = []
    for section in raw_sections:
        cleaned = section.strip()
        if cleaned:
            sections.append(cleaned)
    return sections


def _dedupe_sections(sections: list[str]) -> list[str]:
    seen = set()
    output = []
    for section in sections:
        key = re.sub(r"\W+", "", section.lower())[:300]
        if key in seen:
            continue
        seen.add(key)
        output.append(section)
    return output


def _repair_common_mojibake(text: str) -> str:
    replacements = {
        "â€™": "'",
        "â€˜": "'",
        "â€œ": '"',
        "â€": '"',
        "â€“": "-",
        "â€”": "-",
        "â‚¹": "₹",
        "Â©": "(c)",
        "Â": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text
