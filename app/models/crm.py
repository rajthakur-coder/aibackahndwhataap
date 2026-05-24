from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.db.session import Base


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CustomerMemory(Base):
    __tablename__ = "customer_memories"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    memory_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    intent = Column(String, nullable=True)
    status = Column(String, default="new")
    source = Column(String, default="whatsapp")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    customer_name = Column(String, nullable=True)
    requested_time = Column(String, nullable=True)
    status = Column(String, default="requested")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderStatus(Base):
    __tablename__ = "order_statuses"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=True)
    order_id = Column(String, unique=True, index=True, nullable=False)
    status = Column(String, default="received")
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class HandoffTicket(Base):
    __tablename__ = "handoff_tickets"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=False)
    reason = Column(String, nullable=True)
    status = Column(String, default="open")
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", unique=True, index=True)
    bot_enabled = Column(String, default="true", index=True)
    default_language = Column(String, default="auto")
    welcome_message = Column(Text, default="Welcome! How can I help you today?")
    fallback_message = Column(
        Text,
        default="I do not have that information right now. I can connect you with our support team.",
    )
    offline_message = Column(
        Text,
        default="Our support team is offline right now. Your request is noted and the team will reply during business hours.",
    )
    ai_personality = Column(String, default="helpful")
    ai_tone = Column(String, default="friendly")
    response_length = Column(String, default="brief")
    custom_instructions = Column(Text, nullable=True)
    main_menu_buttons = Column(Text, nullable=True)
    handoff_keywords = Column(Text, nullable=True)
    business_hours_enabled = Column(String, default="false")
    business_hours_start = Column(String, default="09:00")
    business_hours_end = Column(String, default="18:00")
    timezone = Column(String, default="Asia/Kolkata")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgentAction(Base):
    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String, index=True, nullable=True)
    action_type = Column(String, index=True, nullable=False)
    status = Column(String, default="logged")
    payload = Column(Text, nullable=True)
    result = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
