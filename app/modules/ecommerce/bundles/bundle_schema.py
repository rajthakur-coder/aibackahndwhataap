from pydantic import BaseModel, Field


class BundlePairingRequest(BaseModel):
    primary_sku: str
    paired_skus: list[str] = Field(default_factory=list)
    discount_type: str | None = None
    discount_value: str | None = None
    status: str = "active"
    notes: str | None = None


class BundlePairingPatchRequest(BaseModel):
    paired_skus: list[str] | None = None
    discount_type: str | None = None
    discount_value: str | None = None
    status: str | None = None
    notes: str | None = None
