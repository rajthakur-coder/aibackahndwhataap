from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class KnowledgeBase(TimestampMixin, Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", unique=True, index=True)
    website_link = Column(Text, nullable=True)
    company_name = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    about_company = Column(Text, nullable=True)
    target_demographics = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    socials = Column(Text, nullable=True)
    page_images = Column(Text, nullable=True)
    policies = Column(Text, nullable=True)
    faqs = Column(Text, nullable=True)
