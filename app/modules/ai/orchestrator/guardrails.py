import re

from app.modules.ai.orchestrator.response_schema import ToolCallResult


MAX_LINES = 5
MAX_CHARS = 900
UNVERIFIED_DELIVERY_PATTERNS = (
    r"\b(definitely|guaranteed|surely)\s+(arrive|deliver|reach)\b",
    r"\bwill\s+(arrive|be delivered|reach)\s+(today|tomorrow|by)\b",
)
UNVERIFIED_REFUND_PATTERNS = (
    r"\brefund (is|has been) processed\b",
    r"\brefund(ed)?\b.*\b(already|done)\b",
)
CORPORATE_FILLERS = {
    "certainly,": "",
    "absolutely,": "",
    "happy to help": "I can help",
}


def harden_reply(reply: str, tool_result: ToolCallResult | None = None) -> str:
    text = _clean_text(reply)
    text = _remove_unsupported_claims(text, tool_result)
    text = _bound_lines(text)
    return text[:MAX_CHARS].strip() or _fallback(tool_result)


def _clean_text(reply: str) -> str:
    text = str(reply or "").replace("—", ",").replace("–", "-").strip()
    for phrase, replacement in CORPORATE_FILLERS.items():
        text = re.sub(re.escape(phrase), replacement, text, flags=re.I)
    return text


def _remove_unsupported_claims(text: str, tool_result: ToolCallResult | None) -> str:
    data = tool_result.data if tool_result and isinstance(tool_result.data, dict) else {}
    has_delivery_context = bool(data.get("eta") or data.get("tracking_url") or data.get("tracking_number"))
    has_refund_context = bool(tool_result and tool_result.tool_name == "initiate_return" and tool_result.status == "success")

    lines = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            lines.append("")
            continue
        if not has_delivery_context and any(re.search(pattern, candidate, flags=re.I) for pattern in UNVERIFIED_DELIVERY_PATTERNS):
            continue
        if not has_refund_context and any(re.search(pattern, candidate, flags=re.I) for pattern in UNVERIFIED_REFUND_PATTERNS):
            continue
        lines.append(candidate)
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return _fallback(tool_result)
    return cleaned


def _bound_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text.strip()
    compact = []
    for line in lines:
        if len(compact) >= MAX_LINES:
            break
        compact.append(line[:220])
    return "\n".join(compact)


def _fallback(tool_result: ToolCallResult | None) -> str:
    if tool_result and tool_result.message:
        return tool_result.message
    return "I checked the available information. What would you like to do next?"
