from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class CustomerConsent(TimestampMixin, Base):
    __tablename__ = "customer_consents"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    consent_type = Column(String, index=True, nullable=False)
    status = Column(String, default="granted", index=True)
    source = Column(String, default="whatsapp", index=True)
    purpose = Column(Text, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class CSATResponse(TimestampMixin, Base):
    __tablename__ = "csat_responses"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text, nullable=True)
    conversation_id = Column(String, nullable=True, index=True)
    source = Column(String, default="whatsapp", index=True)


class TenantCustomTool(TimestampMixin, Base):
    __tablename__ = "tenant_custom_tools"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    input_schema = Column(Text, nullable=True)
    endpoint_url = Column(Text, nullable=True)
    status = Column(String, default="active", index=True)
    fallback = Column(String, default="create_support_ticket")


class DataPrincipalRequestLog(TimestampMixin, Base):
    __tablename__ = "data_principal_requests"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", index=True)
    phone = Column(String, index=True, nullable=False)
    request_type = Column(String, index=True, nullable=False)
    status = Column(String, default="received", index=True)
    purpose = Column(Text, nullable=True)
    requester_email = Column(String, nullable=True)
    due_at = Column(DateTime, nullable=True, index=True)
    completed_at = Column(DateTime, nullable=True)
    result_summary = Column(Text, nullable=True)
