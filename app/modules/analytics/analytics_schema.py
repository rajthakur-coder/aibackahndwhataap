from pydantic import BaseModel, Field


class CSATRequest(BaseModel):
    phone: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
    conversation_id: str | None = None
