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
