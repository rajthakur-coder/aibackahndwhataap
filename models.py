from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from database import Base


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    message = Column(Text, nullable=False)
    direction = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True)
    name = Column(String, nullable=True)


class ScrapedData(Base):
    __tablename__ = "scraped_data"

    id = Column(Integer, primary_key=True, index=True)
    url = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
