import re


EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\s-]?){10,15}")
ADDRESS_HINT_RE = re.compile(r"\b(flat|house|street|road|sector|block|pincode|pin code|address)\b.*", re.I)


def redact_pii(text: str | None) -> str:
    value = text or ""
    value = EMAIL_RE.sub("[email]", value)
    value = PHONE_RE.sub("[phone]", value)
    value = ADDRESS_HINT_RE.sub("[address]", value)
    return value


def redact_payload(value):
    if isinstance(value, dict):
        return {key: redact_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, str):
        return redact_pii(value)
    return value
