from typing import Any

from pydantic import BaseModel


class KnowledgeBaseRequest(BaseModel):
    website_link: str | None = None
    company_name: str | None = None
    industry: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    about_company: str | None = None
    target_demographics: str | None = None
    logo: str | None = None
    socials: list[dict[str, Any]] = []
    page_images: list[str] = []
    policies: str | None = None
    faqs: str | None = None


class KnowledgeBaseResponse(KnowledgeBaseRequest):
    updated_at: str | None = None
