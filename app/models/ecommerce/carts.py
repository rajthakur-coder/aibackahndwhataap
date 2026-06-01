from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceCart(TimestampMixin, Base):
    __tablename__ = "ecommerce_carts"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    status = Column(String, default="open", index=True)
    items = Column(Text, nullable=True)
    currency = Column(String, nullable=True)
    subtotal = Column(String, nullable=True)
    checkout_url = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
