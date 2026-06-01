from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceReturnRequest(TimestampMixin, Base):
    __tablename__ = "ecommerce_return_requests"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    order_id = Column(String, index=True, nullable=True)
    order_number = Column(String, index=True, nullable=True)
    status = Column(String, default="requested", index=True)
    reason = Column(String, nullable=True)
    item_ids = Column(Text, nullable=True)
    eligibility = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
