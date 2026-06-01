from sqlalchemy import Column, Integer, String, Text

from app.db.mixins import TimestampMixin
from app.db.session import Base


class BotSettings(TimestampMixin, Base):
    __tablename__ = "bot_settings"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String, default="default", unique=True, index=True)
    bot_enabled = Column(String, default="true", index=True)
    default_language = Column(String, default="english")
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
    brand_prompt = Column(Text, nullable=True)
    main_menu_buttons = Column(Text, nullable=True)
    handoff_keywords = Column(Text, nullable=True)
    business_hours_enabled = Column(String, default="false")
    business_hours_start = Column(String, default="09:00")
    business_hours_end = Column(String, default="18:00")
    timezone = Column(String, default="Asia/Kolkata")
