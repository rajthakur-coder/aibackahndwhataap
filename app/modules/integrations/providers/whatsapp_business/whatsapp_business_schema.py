from datetime import datetime

from pydantic import BaseModel, Field

from app.models.integration import IntegrationStatus


WHATSAPP_BUSINESS_STATUSES = {
    IntegrationStatus.CONNECTED,
    IntegrationStatus.NEEDS_REAUTH,
    IntegrationStatus.ERROR,
    IntegrationStatus.DISCONNECTED,
}


class WhatsappBusinessConnectRequest(BaseModel):
    provider: str | None = None
    scopes: list[str] = Field(default_factory=list)
    access_token: str | None = None
    refresh_token: str | None = None
    provider_account_id: str | None = None
    display_name: str | None = None
    status: str = IntegrationStatus.CONNECTED


class WhatsappBusinessDisconnectRequest(BaseModel):
    provider_account_id: str | None = None


class WhatsappBusinessIntegrationOut(BaseModel):
    id: str
    tenant_id: str
    provider: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    provider_account_id: str | None = None
    display_name: str | None = None
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WhatsappBusinessIntegrationResponse(BaseModel):
    status: str = "success"
    integration: WhatsappBusinessIntegrationOut


class WhatsappBusinessIntegrationListResponse(BaseModel):
    status: str = "success"
    integrations: list[WhatsappBusinessIntegrationOut]

