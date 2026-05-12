from pydantic import BaseModel, Field


class MessageTemplateRequest(BaseModel):
    name: str
    body: str
    channel: str = "whatsapp"
    template_type: str = "text"
    provider_template_name: str | None = None
    language: str = "en"
    body_variable_order: list[str] = Field(default_factory=list)
    status: str = "active"


class SendTemplateRequest(BaseModel):
    phone: str
    context: dict = Field(default_factory=dict)


class AutomationRuleRequest(BaseModel):
    name: str
    trigger: str
    message_template_id: int | None = None
    message_body: str | None = None
    delay_seconds: int = 0
    conditions: dict | None = None
    enabled: bool = True


class AutomationRuleUpdateRequest(BaseModel):
    name: str | None = None
    trigger: str | None = None
    message_template_id: int | None = None
    message_body: str | None = None
    delay_seconds: int | None = None
    conditions: dict | None = None
    enabled: bool | None = None


class AutomationEventRequest(BaseModel):
    trigger: str
    source: str = "api"
    external_id: str | None = None
    phone: str | None = None
    payload: dict = Field(default_factory=dict)
    delay_seconds: int = 0


class AbandonedCartRequest(BaseModel):
    phone: str
    cart_url: str | None = None
    customer_name: str | None = None
    total: str | None = None
    currency: str | None = None
    items: list[dict] = Field(default_factory=list)
    external_id: str | None = None
    delay_seconds: int = 0
