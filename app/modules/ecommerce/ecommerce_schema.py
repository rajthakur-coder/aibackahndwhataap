from pydantic import BaseModel, Field


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


class AbandonedCartRequest(BaseModel):
    phone: str
    cart_url: str | None = None
    customer_name: str | None = None
    total: str | None = None
    currency: str | None = None
    items: list[dict] = Field(default_factory=list)
    external_id: str | None = None
    delay_seconds: int = 0


class ShopifyCatalogCollectionSelection(BaseModel):
    shopify_collection_id: str
    visible: bool = True
    sort_order: int = 0


class ShopifyCatalogCollectionUpdateRequest(BaseModel):
    collections: list[ShopifyCatalogCollectionSelection] = Field(default_factory=list)
