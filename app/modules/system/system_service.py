def home_status() -> dict:
    return {
        "status": "ok",
        "message": "AI WhatsApp Automation Backend Running",
    }


def health_status() -> dict:
    return {"status": "healthy"}
