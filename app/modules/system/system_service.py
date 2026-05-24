def home_status() -> dict:
    return {
        "status": "ok",
        "message": "AI WhatsApp Automation Backend Running",
    }


def health_status() -> dict:
    return {"status": "healthy"}


def runtime_config_status() -> dict:
    from app.config import settings

    return {
        "shopify_webhook_automation_enabled": settings.shopify_webhook_automation_enabled,
        "automation_processor_enabled": settings.automation_processor_enabled,
    }
