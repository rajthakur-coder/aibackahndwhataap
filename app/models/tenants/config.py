from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class TenantConfig(TimestampMixin, Base):
    __tablename__ = "tenant_configs"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_configs_tenant_id"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), nullable=False, index=True)
    brand_name = Column(String(160), nullable=False)
    brand_voice_prompt = Column(Text, nullable=True)
    return_policy = Column(Text, nullable=True)
    shipping_policy = Column(Text, nullable=True)
    warranty_policy = Column(Text, nullable=True)
    discount_rules = Column(Text, nullable=True)
    categories = Column(Text, nullable=True)
    support_email = Column(String(255), nullable=True)
    support_sla_hours = Column(Integer, default=4)
    default_emoji = Column(String(16), nullable=True)
    default_tone = Column(String(80), nullable=True)
    metadata_json = Column(Text, nullable=True)
