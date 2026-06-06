import re

from app.config import settings


def normalize_whatsapp_recipient(phone: str) -> str:
    digits = re.sub(r"\D+", "", str(phone or ""))
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    country_code = str(getattr(settings, "WHATSAPP_DEFAULT_COUNTRY_CODE", "91") or "91").strip().lstrip("+")
    if country_code and len(digits) == 10:
        return f"{country_code}{digits}"
    return digits


def raise_for_whatsapp_response(response, action: str) -> None:
    if response.status_code < 400:
        return
    try:
        detail = response.json()
    except ValueError:
        detail = response.text
    raise RuntimeError(f"{action} failed: {response.status_code} {detail}")


__all__ = ["normalize_whatsapp_recipient", "raise_for_whatsapp_response"]
