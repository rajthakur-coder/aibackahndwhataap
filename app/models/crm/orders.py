from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class OrderStatus(TimestampMixin, Base):
    __tablename__ = "order_statuses"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=True)
    order_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="received")
    details = Column(Text, nullable=True)
