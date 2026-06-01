from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class EcommerceBundlePairing(TimestampMixin, Base):
    __tablename__ = "ecommerce_bundle_pairings"
    __table_args__ = (UniqueConstraint("tenant_id", "primary_sku", name="uq_bundle_pairing_tenant_primary_sku"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    primary_sku = Column(String, nullable=False, index=True)
    paired_skus = Column(Text, nullable=True)
    discount_type = Column(String, nullable=True)
    discount_value = Column(String, nullable=True)
    status = Column(String, default="active", index=True)
    notes = Column(Text, nullable=True)
