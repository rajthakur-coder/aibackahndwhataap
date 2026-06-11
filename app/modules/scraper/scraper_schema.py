from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, HttpUrl


class ScraperSocialOut(BaseModel):
    type: str
    url: str


class ScraperCompetitorOut(BaseModel):
    name: str
    url: str = ""


class ScraperResultOut(BaseModel):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    about_company: Optional[str] = None
    website_link: Optional[str] = None
    logo: Optional[str] = None
    color_palette: List[str] = []
    fonts: List[str] = []
    target_demographics: Optional[str] = None
    policies: Optional[str] = None
    faqs: Optional[str] = None
    socials: List[ScraperSocialOut] = []
    competitors: List[ScraperCompetitorOut] = []
    page_images: List[str] = []


class ScraperResponse(BaseModel):
    status: str = "success"
    data: ScraperResultOut


class ScraperInput(BaseModel):
    website_link: HttpUrl

    class Config:
        extra = "ignore"
