from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(80), default="default", index=True, nullable=False)
    phone = Column(String, index=True, nullable=False)
    customer_name = Column(String, nullable=True)
    requested_time = Column(String, nullable=True)
    status = Column(String, default="requested")
    notes = Column(Text, nullable=True)
