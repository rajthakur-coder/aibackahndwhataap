import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV = os.getenv("ENV", "local")


class Settings(BaseSettings):
    APP_NAME: str
    APP_URL: str
    PUBLIC_WEBHOOK_BASE_URL: str
    PORT: int
    DEBUG: bool
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str]
    INIT_DB_ON_STARTUP: bool

    DATABASE_URI: str
    REDIS_URL: str

    SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    COOKIE_DOMAIN: str | None = None
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"

    GMAIL_ID: str
    GMAIL_APP_PASSWORD: str
    ALIGNAUTH_APP_URL: str
    ALIGNADS_APP_URL: str = ""
    API_BASE_URL: str = ""
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    ARQ_QUEUE_NAME: str = "alignlabs:jobs"
    ARQ_DEFAULT_TIMEOUT: int = 900
    MAX_CONCURRENT_JOBS: int = 5
    JOB_MAX_ATTEMPTS: int = 3

    META_APP_ID: str
    META_APP_SECRET: str
    WHATSAPP_BASE_URL: str = "https://graph.facebook.com/v25.0"
    ACCESS_TOKEN: str
    PHONE_NUMBER_ID: str
    WHATSAPP_CATALOG_ID: str
    VERIFY_TOKEN: str
    WHATSAPP_WEBHOOK_APP_SECRET: str = ""
    WHATSAPP_REGISTER_PIN: str = ""
    WHATSAPP_DATA_LOCALIZATION_REGION: str = ""

    OPENROUTER_API_KEY: str
    OPENROUTER_MODEL: str
    ROUTER_MODEL: str
    OPENROUTER_TIMEOUT_SECONDS: int
    FIRECRAWL_API_KEY: str
    PERPLEXITY_API_KEY: str

    SHOPIFY_WEBHOOK_SECRET: str
    SHOPIFY_REQUIRED_SCOPES: list[str]
    ECOMMERCE_TOKEN_SECRET: str
    ECOMMERCE_AUTO_SYNC_CHECKOUTS_ENABLED: bool
    ECOMMERCE_AUTO_SYNC_INTERVAL_SECONDS: int
    ECOMMERCE_AUTO_SYNC_LIMIT: int
    ECOMMERCE_AUTO_SYNC_PRODUCTS_ENABLED: bool
    ECOMMERCE_AUTO_SYNC_PRODUCT_LIMIT: int
    SHOPIFY_WEBHOOK_AUTOMATION_ENABLED: bool
    SHOPIFY_PRODUCT_CACHE_TTL_SECONDS: int
    SHOPIFY_QUERY_CACHE_TTL_SECONDS: int
    SHOPIFY_ORDER_CACHE_TTL_SECONDS: int

    AUTOMATION_PROCESSOR_ENABLED: bool
    AUTOMATION_PROCESSOR_INTERVAL_SECONDS: int
    AUTOMATION_PROCESSOR_LIMIT: int
    ABANDONED_CART_DELAY_SECONDS: int

    @field_validator("DEBUG", mode="before")
    @classmethod
    def parse_debug_aliases(cls, value):
        if isinstance(value, str) and value.strip().lower() in {"release", "prod", "production"}:
            return False
        return value

    @field_validator("COOKIE_SAMESITE", mode="before")
    @classmethod
    def normalize_cookie_samesite(cls, value):
        normalized = str(value or "lax").strip().lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("COOKIE_SAMESITE must be one of: lax, strict, none")
        return normalized

    model_config = SettingsConfigDict(
        env_file=(".env", f".env.{ENV}"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
