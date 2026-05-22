import os

from pydantic_settings import BaseSettings, SettingsConfigDict


ENV = os.getenv("ENV", "local")


class Settings(BaseSettings):
    app_name: str = "AI WhatsApp Automation"
    app_url: str = ""
    public_webhook_base_url: str = ""
    shopify_webhook_secret: str = ""
    shopify_required_scopes: str = (
        "read_products,read_inventory,read_orders,read_customers,"
        "read_checkouts,read_fulfillments,read_locations"
    )
    ecommerce_token_secret: str = ""
    cors_origins: list[str] = ["*"]
    database_url: str = "sqlite:///./app.db"
    redis_url: str = "redis://localhost:6379/0"
    debug: str | bool = False
    port: int = 8000
    init_db_on_startup: bool = True

    meta_app_id: str = ""
    meta_app_secret: str = ""
    whatsapp_base_url: str = "https://graph.facebook.com/v25.0"
    access_token: str = ""
    phone_number_id: str = ""
    whatsapp_catalog_id: str = ""
    verify_token: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o"
    router_model: str = ""

    ecommerce_auto_sync_enabled: bool = True
    ecommerce_auto_sync_checkouts_enabled: bool = False
    ecommerce_auto_sync_interval_seconds: int = 300
    ecommerce_auto_sync_limit: int = 50
    ecommerce_auto_sync_products_enabled: bool = True
    ecommerce_auto_sync_product_limit: int = 100
    shopify_webhook_automation_enabled: bool = False
    shopify_product_cache_ttl_seconds: int = 300
    shopify_query_cache_ttl_seconds: int = 300
    shopify_order_cache_ttl_seconds: int = 60
    automation_processor_enabled: bool = True
    automation_processor_interval_seconds: int = 60
    automation_processor_limit: int = 50

    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{ENV}"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def DATABASE_URI(self) -> str:
        return self.database_url

    @property
    def REDIS_URL(self) -> str:
        return self.redis_url

    @property
    def SHOPIFY_REQUIRED_SCOPES(self) -> list[str]:
        return [
            scope.strip()
            for scope in self.shopify_required_scopes.split(",")
            if scope.strip()
        ]

    @property
    def DEBUG(self) -> bool:
        if isinstance(self.debug, bool):
            return self.debug
        return self.debug.strip().lower() in {"1", "true", "yes", "on", "debug", "development", "local"}


settings = Settings()
