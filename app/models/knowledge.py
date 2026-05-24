from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.session import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", unique=True, index=True)
    website_link = Column(Text, nullable=True)
    company_name = Column(String, nullable=True)
    industry = Column(String, nullable=True)
    about_company = Column(Text, nullable=True)
    target_demographics = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    socials = Column(Text, nullable=True)
    page_images = Column(Text, nullable=True)
    policies = Column(Text, nullable=True)
    faqs = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
