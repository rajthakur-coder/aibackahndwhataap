import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_FILE)


class Settings:
    app_name = os.getenv("APP_NAME", "AI WhatsApp Automation")
    app_url = os.getenv("APP_URL", "")
    cors_origins = os.getenv("CORS_ORIGINS", "*").split(",")
    database_url = os.getenv("DATABASE_URL", "sqlite:///./app.db")
    port = int(os.getenv("PORT", "8000"))

    ecommerce_auto_sync_enabled = os.getenv(
        "ECOMMERCE_AUTO_SYNC_ENABLED",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    ecommerce_auto_sync_interval_seconds = max(
        60,
        int(os.getenv("ECOMMERCE_AUTO_SYNC_INTERVAL_SECONDS", "300")),
    )
    ecommerce_auto_sync_limit = max(
        1,
        min(int(os.getenv("ECOMMERCE_AUTO_SYNC_LIMIT", "50")), 100),
    )
    ecommerce_auto_sync_products_enabled = os.getenv(
        "ECOMMERCE_AUTO_SYNC_PRODUCTS_ENABLED",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}
    ecommerce_auto_sync_product_limit = max(
        1,
        min(int(os.getenv("ECOMMERCE_AUTO_SYNC_PRODUCT_LIMIT", "100")), 250),
    )


settings = Settings()
