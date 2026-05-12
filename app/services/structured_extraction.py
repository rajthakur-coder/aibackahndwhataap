import json
import os
import re
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from app.models.entities import FAQ, Policy, ScrapedData, Service, StructuredProduct
from app.services.intelligence import detect_policy_type


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
QUESTION_RE = re.compile(r"(.+\?)\s*(.+)", re.DOTALL)
PRICE_RE = re.compile(r"(?:rs\.?|inr|₹|\$)\s?[\d,]+(?:\.\d{1,2})?", re.IGNORECASE)


def save_structured_extractions(db: Session, scraped_data: ScrapedData) -> dict:
    payload = extract_structured_data(scraped_data.url, scraped_data.content)
    _replace_existing(db, scraped_data.url)

    counts = {"products": 0, "faqs": 0, "policies": 0, "services": 0}
    for product in payload.get("products", []):
        title = _clean(product.get("title") or product.get("name"))
        if not title:
            continue
        db.add(
            StructuredProduct(
                source_url=scraped_data.url,
                title=title,
                description=_clean(product.get("description")),
                category=_clean(product.get("category")),
                brand=_clean(product.get("brand")),
                price=_clean(product.get("price")),
                image_urls=json.dumps(product.get("image_urls") or []),
                raw_payload=json.dumps(product),
            )
        )
        counts["products"] += 1

    for faq in payload.get("faqs", []):
        question = _clean(faq.get("question"))
        answer = _clean(faq.get("answer"))
        if not question or not answer:
            continue
        db.add(
            FAQ(
                source_url=scraped_data.url,
                question=question,
                answer=answer,
                category=_clean(faq.get("category")),
                raw_payload=json.dumps(faq),
            )
        )
        counts["faqs"] += 1

    for policy in payload.get("policies", []):
        content = _clean(policy.get("content") or policy.get("answer"))
        if not content:
            continue
        policy_type = _clean(policy.get("policy_type")) or detect_policy_type(content) or "general"
        db.add(
            Policy(
                source_url=scraped_data.url,
                policy_type=policy_type,
                title=_clean(policy.get("title")) or policy_type.title(),
                content=content,
                raw_payload=json.dumps(policy),
            )
        )
        counts["policies"] += 1

    for service in payload.get("services", []):
        name = _clean(service.get("name") or service.get("title"))
        if not name:
            continue
        db.add(
            Service(
                source_url=scraped_data.url,
                name=name,
                description=_clean(service.get("description")),
                category=_clean(service.get("category")),
                price=_clean(service.get("price")),
                raw_payload=json.dumps(service),
            )
        )
        counts["services"] += 1

    db.commit()
    return counts


def extract_structured_data(url: str, content: str) -> dict:
    if (
        _should_use_ai_extraction(url, content)
        and os.getenv("AI_EXTRACTION_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    ):
        ai_payload = _extract_with_ai(url, content)
        if ai_payload:
            return _normalize_payload(ai_payload)
    return _extract_with_heuristics(url, content)


def _should_use_ai_extraction(url: str, content: str) -> bool:
    metadata = _page_metadata(content)
    page_type = str(metadata.get("page_type") or "").lower()
    if page_type in {"faq", "policy", "contact", "about"}:
        return True

    path = urlparse(url).path.lower()
    important_terms = (
        "about",
        "contact",
        "faq",
        "help",
        "policy",
        "privacy",
        "refund",
        "return",
        "shipping",
        "terms",
    )
    return any(term in path for term in important_terms)


def _extract_with_ai(url: str, content: str) -> dict | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    try:
        response = requests.post(
            OPENROUTER_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("APP_URL", ""),
                "X-Title": os.getenv("APP_NAME", "AI WhatsApp Automation"),
            },
            json={
                "model": os.getenv("EXTRACTION_MODEL", os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")),
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Extract structured business data from website text. "
                            "Return only JSON with keys products, faqs, policies, services. "
                            "Do not invent missing data."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"source_url": url, "content": content[:12000]},
                            ensure_ascii=True,
                        ),
                    },
                ],
                "temperature": 0,
                "max_tokens": 1200,
            },
            timeout=35,
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        return json.loads(match.group(0) if match else text)
    except Exception as exc:
        print("STRUCTURED EXTRACTION AI ERROR:", exc)
        return None


def _extract_with_heuristics(url: str, content: str) -> dict:
    metadata = _page_metadata(content)
    sections = _sections(content)
    payload = {"products": [], "faqs": [], "policies": [], "services": []}

    if metadata.get("page_type") == "product":
        payload["products"].append(
            {
                "title": metadata.get("title") or _page_title(content),
                "description": _first_section_text(sections),
                "category": metadata.get("category"),
                "brand": metadata.get("brand"),
                "price": metadata.get("price") or _first_price(content),
                "image_urls": _image_urls(content),
            }
        )

    for heading, text in sections:
        lowered = f"{heading}\n{text}".lower()
        if "faq" in lowered or "?" in text:
            question, answer = _faq_from_section(heading, text)
            if question and answer:
                payload["faqs"].append({"question": question, "answer": answer, "category": heading})
                continue

        policy_type = detect_policy_type(lowered)
        if policy_type or "policy" in lowered:
            payload["policies"].append(
                {
                    "policy_type": policy_type or "general",
                    "title": heading,
                    "content": text,
                }
            )
            continue

        if any(term in lowered for term in ("service", "experience", "package", "plan")):
            payload["services"].append(
                {
                    "name": heading if heading != "Page content" else (_page_title(content) or "Service"),
                    "description": text,
                    "category": metadata.get("category"),
                    "price": _first_price(text),
                }
            )

    return _normalize_payload(payload)


def _replace_existing(db: Session, source_url: str) -> None:
    for model in (StructuredProduct, FAQ, Policy, Service):
        db.query(model).filter(model.source_url == source_url).delete()


def _normalize_payload(payload: dict) -> dict:
    return {
        "products": _list(payload.get("products")),
        "faqs": _list(payload.get("faqs")),
        "policies": _list(payload.get("policies")),
        "services": _list(payload.get("services")),
    }


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _page_metadata(content: str) -> dict:
    match = re.search(r"(?m)^Page metadata:\s*(\{.+\})$", content or "")
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _page_title(content: str) -> str:
    match = re.search(r"(?m)^Page title:\s*(.+)$", content or "")
    return _clean(match.group(1)) if match else ""


def _sections(content: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^Section:\s*(.+)$", content or ""))
    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        sections.append((_clean(match.group(1)), content[start:end].strip()))
    return sections


def _first_section_text(sections: list[tuple[str, str]]) -> str:
    return _clean(sections[0][1]) if sections else ""


def _first_price(text: str) -> str:
    match = PRICE_RE.search(text or "")
    return match.group(0) if match else ""


def _image_urls(content: str) -> list[str]:
    match = re.search(r"(?m)^Images:\s*(.+)$", content or "")
    if not match:
        return []
    return re.findall(r"https?://[^\s,]+", match.group(1))


def _faq_from_section(heading: str, text: str) -> tuple[str, str]:
    if "?" in heading:
        return heading, _clean(text)
    match = QUESTION_RE.match(_clean(text))
    if match:
        return _clean(match.group(1)), _clean(match.group(2))
    return "", ""
