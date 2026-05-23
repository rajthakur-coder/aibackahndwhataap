from pydantic import BaseModel


class OrderRequest(BaseModel):
    order_id: str
    status: str
    phone: str | None = None
    details: str | None = None


class ActionRequest(BaseModel):
    phone: str
    payload: dict


class HandoffResolveRequest(BaseModel):
    note: str | None = None


class BotSettingsRequest(BaseModel):
    bot_enabled: bool = True
    default_language: str = "auto"
    welcome_message: str | None = None
    fallback_message: str | None = None
    offline_message: str | None = None
    main_menu_buttons: list[dict] = []
    handoff_keywords: list[str] = []
    business_hours_enabled: bool = False
    business_hours_start: str = "09:00"
    business_hours_end: str = "18:00"
    timezone: str = "Asia/Kolkata"
