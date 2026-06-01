from pydantic import BaseModel, Field


class TenantConfigRequest(BaseModel):
    brand_name: str | None = None
    brand_voice_prompt: str | None = None
    return_policy: str | None = None
    shipping_policy: str | None = None
    warranty_policy: str | None = None
    discount_rules: list[dict] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    support_email: str | None = None
    support_sla_hours: int | None = None
    default_emoji: str | None = None
    default_tone: str | None = None
    metadata: dict = Field(default_factory=dict)


class TenantTemplateSeedRequest(BaseModel):
    template: str = "commerce"
    overwrite: bool = False


class TenantConfigResponse(BaseModel):
    tenant_id: str
    brand_name: str
    brand_voice_prompt: str | None = None
    return_policy: str | None = None
    shipping_policy: str | None = None
    warranty_policy: str | None = None
    discount_rules: list[dict] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    support_email: str | None = None
    support_sla_hours: int | None = None
    default_emoji: str | None = None
    default_tone: str | None = None
    metadata: dict = Field(default_factory=dict)
