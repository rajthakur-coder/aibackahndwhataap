from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Lead(TimestampMixin, Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="new")
    source = Column(String, default="whatsapp")
    notes = Column(Text, nullable=True)
