PROHIBITED_SERVICE_WORDS = {"guaranteed profit", "miracle", "free money", "100% cure"}
PROMO_HINTS = {"sale", "discount", "offer", "buy now", "limited time", "free shipping"}


def check_template_compliance(payload: dict) -> dict:
    text = _template_text(payload).lower()
    issues = []
    for phrase in PROHIBITED_SERVICE_WORDS:
        if phrase in text:
            issues.append({"code": "prohibited_claim", "message": f"Remove prohibited claim: {phrase}"})
    category = str(payload.get("category") or "").upper()
    if category in {"UTILITY", "AUTHENTICATION"} and any(term in text for term in PROMO_HINTS):
        issues.append({"code": "promotional_in_service_template", "message": "Promotional wording should use a marketing template."})
    return {"ok": not issues, "issues": issues}


def _template_text(payload: dict) -> str:
    parts = [str(payload.get("name") or ""), str(payload.get("category") or "")]
    for component in payload.get("components") or []:
        if isinstance(component, dict):
            parts.append(str(component.get("text") or ""))
    return " ".join(parts)
