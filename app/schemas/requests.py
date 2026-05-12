from pydantic import BaseModel


class SendMessageRequest(BaseModel):
    phone: str
    message: str


class ScrapeRequest(BaseModel):
    url: str
    max_pages: int = 20


class DocumentRequest(BaseModel):
    title: str
    content: str
    source: str | None = None


class OrderRequest(BaseModel):
    order_id: str
    status: str
    phone: str | None = None
    details: str | None = None


class ActionRequest(BaseModel):
    phone: str
    payload: dict


class EcommerceConnectionRequest(BaseModel):
    name: str
    platform: str
    store_url: str
    access_token: str | None = None
    consumer_key: str | None = None
    consumer_secret: str | None = None


class EcommerceConnectionUpdateRequest(BaseModel):
    name: str | None = None
    store_url: str | None = None
    access_token: str | None = None
    consumer_key: str | None = None
    consumer_secret: str | None = None
    status: str | None = None


class EcommerceSyncRequest(BaseModel):
    limit: int = 50


class EcommerceProductSyncRequest(BaseModel):
    limit: int = 100


class DeliveredFollowupRequest(BaseModel):
    limit: int = 25


class RetryWebhookEventsRequest(BaseModel):
    limit: int = 25
