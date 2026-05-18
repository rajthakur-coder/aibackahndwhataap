from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    phone: str
    message: str


class RetryWebhookEventsRequest(BaseModel):
    limit: int = 25


class WhatsappNumberSetupRequest(BaseModel):
    authorization_token: str
    phone_number_id: str
    waba_id: str
    business_id: str


class WhatsappCredentialResponse(BaseModel):
    id: int
    tenant_id: str | None = None
    waba_id: str | None = None
    business_id: str | None = None
    phone_number_id: str | None = None
    phone_number: str | None = None
    status: str | None = None
    business_name: str | None = None
    verified_name: str | None = None
    name: str | None = None
    callback_url: str | None = None
    nerochat_callback_url: str | None = None
