from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from app.db.mixins import TimestampMixin
from app.db.session import Base


class AgencyTenantAccess(TimestampMixin, Base):
    __tablename__ = "agency_tenant_access"
    __table_args__ = (UniqueConstraint("agency_tenant_id", "client_tenant_id", name="uq_agency_client_tenant"),)

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), nullable=False, index=True)
    agency_tenant_id = Column(String(80), nullable=False, index=True)
    client_tenant_id = Column(String(80), nullable=False, index=True)
    role = Column(String(40), default="reseller_admin", index=True)
    status = Column(String(40), default="active", index=True)
    white_label_name = Column(String(160), nullable=True)
    white_label_domain = Column(String(255), nullable=True)
    support_email = Column(String(255), nullable=True)
    settings_json = Column(Text, nullable=True)
