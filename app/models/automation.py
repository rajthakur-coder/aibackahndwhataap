from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class MessageTemplate(TimestampMixin, Base):
    __tablename__ = "message_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    body = Column(Text, nullable=False)
    channel = Column(String, default="whatsapp", index=True)
    template_type = Column(String, default="text", index=True)
    provider_template_name = Column(String, nullable=True, index=True)
    language = Column(String, default="en")
    body_variable_order = Column(Text, nullable=True)
    status = Column(String, default="active", index=True)


class AutomationRule(TimestampMixin, Base):
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    trigger = Column(String, index=True, nullable=False)
    message_template_id = Column(Integer, nullable=True, index=True)
    message_body = Column(Text, nullable=True)
    delay_seconds = Column(Integer, default=0)
    conditions = Column(Text, nullable=True)
    enabled = Column(String, default="true", index=True)


class AutomationEvent(TimestampMixin, Base):
    __tablename__ = "automation_events"

    id = Column(Integer, primary_key=True, index=True)
    trigger = Column(String, index=True, nullable=False)
    source = Column(String, default="system", index=True)
    external_id = Column(String, nullable=True, index=True)
    phone = Column(String, nullable=True, index=True)
    payload = Column(Text, nullable=True)
    status = Column(String, default="pending", index=True)
    scheduled_for = Column(DateTime, default=datetime.utcnow, index=True)
    processed_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)


class AutomationExecution(TimestampMixin, Base):
    __tablename__ = "automation_executions"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, index=True, nullable=False)
    rule_id = Column(Integer, index=True, nullable=False)
    phone = Column(String, nullable=True, index=True)
    status = Column(String, default="pending", index=True)
    rendered_message = Column(Text, nullable=True)
    provider_response = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True)
