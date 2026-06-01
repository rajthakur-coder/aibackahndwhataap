from datetime import datetime

from pydantic import BaseModel, Field

from app.models.integration import IntegrationStatus


WOOCOMMERCE_STATUSES = {
    IntegrationStatus.CONNECTED,
    IntegrationStatus.NEEDS_REAUTH,
    IntegrationStatus.ERROR,
    IntegrationStatus.DISCONNECTED,
}


class WoocommerceConnectRequest(BaseModel):
    provider: str | None = None
    scopes: list[str] = Field(default_factory=list)
    access_token: str | None = None
    refresh_token: str | None = None
    provider_account_id: str | None = None
    display_name: str | None = None
    status: str = IntegrationStatus.CONNECTED


class WoocommerceDisconnectRequest(BaseModel):
    provider_account_id: str | None = None


class WoocommerceIntegrationOut(BaseModel):
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


class WoocommerceIntegrationResponse(BaseModel):
    status: str = "success"
    integration: WoocommerceIntegrationOut


class WoocommerceIntegrationListResponse(BaseModel):
    status: str = "success"
    integrations: list[WoocommerceIntegrationOut]

