from pydantic import BaseModel, Field


class WebsiteAssistRequest(BaseModel):
    website_url: str
    apply: bool = True


class BundleSuggestionRequest(BaseModel):
    limit: int = 20
    apply: bool = False
    discount_type: str | None = None
    discount_value: str | None = None


class ApplyBundleSuggestionsRequest(BaseModel):
    suggestions: list[dict] = Field(default_factory=list)
    discount_type: str | None = None
    discount_value: str | None = None


class OnboardingStepUpdateRequest(BaseModel):
    status: str = "completed"
    data: dict = Field(default_factory=dict)


class OnboardingPreviewRequest(BaseModel):
    phone: str | None = None
    message: str = "Hi"
    channel: str = "sandbox"
