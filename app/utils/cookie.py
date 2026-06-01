from app.config import settings


def get_cookie_options(key: str, value: str, max_age: int = 3600) -> dict:
    return {
        "key": key,
        "value": value,
        "max_age": max_age,
        "path": "/",
        "domain": settings.COOKIE_DOMAIN or None,
        "secure": settings.COOKIE_SECURE,
        "httponly": True,
        "samesite": "lax",
    }


def get_delete_cookie_options(key: str) -> dict:
    return {
        "key": key,
        "path": "/",
        "domain": settings.COOKIE_DOMAIN or None,
        "secure": settings.COOKIE_SECURE,
        "httponly": True,
        "samesite": "lax",
    }
