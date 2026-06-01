from pydantic import BaseModel


class ConsentRequest(BaseModel):
    phone: str
    consent_type: str = "marketing"
    status: str = "granted"
    purpose: str | None = None


class DataPrincipalRequest(BaseModel):
    phone: str
    request_type: str = "export"
    requester_email: str | None = None
    purpose: str | None = None


class DataPrincipalResolveRequest(BaseModel):
    request_id: int
    status: str = "completed"
    result_summary: str | None = None
