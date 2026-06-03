from app.config import settings


def _cookie_secure() -> bool:
    return settings.COOKIE_SECURE or settings.COOKIE_SAMESITE == "none"


def _base_cookie_options(key: str) -> dict:
    return {
        "key": key,
        "path": "/",
        "domain": settings.COOKIE_DOMAIN or None,
        "secure": _cookie_secure(),
        "httponly": True,
        "samesite": settings.COOKIE_SAMESITE,
    }


def get_cookie_options(key: str, value: str, max_age: int = 3600) -> dict:
    return {
        **_base_cookie_options(key),
        "value": value,
        "max_age": max_age,
    }


def get_delete_cookie_options(key: str) -> dict:
    return {
        **_base_cookie_options(key),
    }
