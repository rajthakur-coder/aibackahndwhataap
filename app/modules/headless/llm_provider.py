from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from sqlalchemy.orm import Session

from app.config import settings
from app.models.crm import AgentAction
from app.modules.tenants.tenant_service import get_tenant_config, serialize_tenant_config
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT = 20
MODEL_COST_PER_1K = {
    "anthropic/claude-3.5-haiku": 0.0008,
    "anthropic/claude-sonnet-4": 0.006,
    "openai/gpt-4.1": 0.01,
    "openai/gpt-4o-mini": 0.0006,
    "gpt-4.1": 0.01,
    "o4-mini": 0.0011,
    "gemini-pro": 0.0005,
}


@dataclass(frozen=True)
class LLMProviderConfig:
    provider: str
    model: str
    enabled: bool = True
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


@dataclass(frozen=True)
class NormalizedToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    provider: str
    model: str
    content: str
    tool_calls: list[NormalizedToolCall] = field(default_factory=list)
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw: dict[str, Any] = field(default_factory=dict)
    fallback_used: bool = False


class LLMProviderRegistry:
    def __init__(self) -> None:
        self._providers = {
            "openrouter": ["anthropic/claude-sonnet-4", "anthropic/claude-3.5-haiku", "openai/gpt-4.1", "openai/gpt-4o-mini", "google/gemini-pro"],
            "openai": ["gpt-4.1", "o4-mini"],
            "anthropic": ["claude-sonnet-4", "claude-3-5-haiku-latest"],
            "gemini": ["gemini-pro", "gemini-1.5-flash"],
            "custom": [],
        }

    def list_providers(self) -> dict:
        return dict(self._providers)

    def validate(self, provider: str, model: str) -> bool:
        provider_key = provider.strip().lower()
        return provider_key in self._providers and (not self._providers[provider_key] or model in self._providers[provider_key])


llm_provider_registry = LLMProviderRegistry()


def tenant_llm_candidates(
    db: Session | None,
    tenant_id: str = DEFAULT_TENANT_ID,
    *,
    purpose: str = "reply",
) -> list[LLMProviderConfig]:
    tenant_id = normalize_tenant_id(tenant_id)
    metadata = _tenant_metadata(db, tenant_id)
    llm = metadata.get("llm") if isinstance(metadata.get("llm"), dict) else {}
    purpose_cfg = llm.get(purpose) if isinstance(llm.get(purpose), dict) else {}
    primary = {**llm, **purpose_cfg}

    provider = str(primary.get("provider") or "openrouter").strip().lower()
    model = str(
        primary.get("model")
        or (settings.ROUTER_MODEL if purpose == "tool_choice" else settings.OPENROUTER_MODEL)
        or "openai/gpt-4o-mini"
    ).strip()
    candidates = [LLMProviderConfig(provider=provider, model=model, base_url=primary.get("base_url"), api_key_env=primary.get("api_key_env"))]

    fallbacks = primary.get("fallbacks") or llm.get("fallbacks") or []
    if isinstance(fallbacks, list):
        for item in fallbacks:
            if not isinstance(item, dict):
                continue
            fallback_provider = str(item.get("provider") or provider).strip().lower()
            fallback_model = str(item.get("model") or "").strip()
            if fallback_model:
                candidates.append(
                    LLMProviderConfig(
                        provider=fallback_provider,
                        model=fallback_model,
                        base_url=item.get("base_url"),
                        api_key_env=item.get("api_key_env"),
                    )
                )

    candidates.append(LLMProviderConfig(provider="openrouter", model=settings.OPENROUTER_MODEL or "openai/gpt-4o-mini"))
    return _dedupe_enabled(candidates)


def chat_completion(
    db: Session | None,
    *,
    tenant_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    purpose: str = "reply",
    temperature: float = 0.4,
    max_tokens: int = 320,
) -> LLMResponse:
    tenant_id = normalize_tenant_id(tenant_id)
    errors = []
    for index, config in enumerate(tenant_llm_candidates(db, tenant_id, purpose=purpose)):
        started = time.perf_counter()
        try:
            response = _call_provider(config, messages, tools or [], temperature, max_tokens)
            latency_ms = int((time.perf_counter() - started) * 1000)
            _log_llm_call(db, tenant_id, purpose, config, response, latency_ms, fallback_used=index > 0)
            if index > 0:
                return LLMResponse(**{**response.__dict__, "fallback_used": True})
            return response
        except Exception as exc:
            errors.append({"provider": config.provider, "model": config.model, "error": str(exc)})
            _log_llm_failure(db, tenant_id, purpose, config, exc)
            continue
    raise RuntimeError("All LLM providers failed: " + json.dumps(errors, ensure_ascii=True)[:2000])


def normalize_tool_choice_response(response: LLMResponse) -> tuple[str, dict] | None:
    if response.tool_calls:
        call = response.tool_calls[0]
        return call.name, call.arguments
    try:
        data = json.loads(response.content)
    except json.JSONDecodeError:
        return None
    name = str(data.get("tool") or data.get("name") or "").strip()
    arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    return (name, arguments) if name else None


def _call_provider(
    config: LLMProviderConfig,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    max_tokens: int,
) -> LLMResponse:
    provider = config.provider.strip().lower()
    if provider == "openrouter":
        return _call_openai_compatible(config, OPENROUTER_CHAT_URL, _api_key(config, "OPENROUTER_API_KEY", settings.OPENROUTER_API_KEY), messages, tools, temperature, max_tokens)
    if provider == "openai":
        return _call_openai_compatible(config, config.base_url or OPENAI_CHAT_URL, _api_key(config, "OPENAI_API_KEY", ""), messages, tools, temperature, max_tokens)
    if provider == "anthropic":
        return _call_anthropic(config, messages, tools, temperature, max_tokens)
    if provider == "gemini":
        return _call_gemini(config, messages, tools, temperature, max_tokens)
    if provider == "custom":
        return _call_openai_compatible(config, config.base_url or OPENROUTER_CHAT_URL, _api_key(config, "CUSTOM_LLM_API_KEY", settings.OPENROUTER_API_KEY), messages, tools, temperature, max_tokens)
    raise ValueError(f"Unsupported LLM provider: {config.provider}")


def _call_openai_compatible(
    config: LLMProviderConfig,
    url: str,
    api_key: str,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    max_tokens: int,
) -> LLMResponse:
    if not api_key:
        raise RuntimeError(f"{config.provider} API key is not configured")
    payload = {"model": config.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    response = requests.post(url, headers=_openai_headers(api_key), json=payload, timeout=_timeout())
    response.raise_for_status()
    return _normalize_openai_response(config, response.json(), messages)


def _call_anthropic(
    config: LLMProviderConfig,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    max_tokens: int,
) -> LLMResponse:
    api_key = _api_key(config, "ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    system, anthropic_messages = _anthropic_messages(messages)
    payload = {
        "model": config.model,
        "system": system,
        "messages": anthropic_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = [_anthropic_tool(tool) for tool in tools]
    response = requests.post(
        ANTHROPIC_MESSAGES_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        json=payload,
        timeout=_timeout(),
    )
    response.raise_for_status()
    return _normalize_anthropic_response(config, response.json(), messages)


def _call_gemini(
    config: LLMProviderConfig,
    messages: list[dict],
    tools: list[dict],
    temperature: float,
    max_tokens: int,
) -> LLMResponse:
    api_key = _api_key(config, "GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    payload = {
        "contents": _gemini_contents(messages),
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if tools:
        payload["tools"] = [{"functionDeclarations": [_gemini_tool(tool) for tool in tools]}]
    response = requests.post(
        GEMINI_URL_TEMPLATE.format(model=config.model),
        params={"key": api_key},
        json=payload,
        timeout=_timeout(),
    )
    response.raise_for_status()
    return _normalize_gemini_response(config, response.json(), messages)


def _normalize_openai_response(config: LLMProviderConfig, payload: dict, messages: list[dict]) -> LLMResponse:
    message = ((payload.get("choices") or [{}])[0].get("message") or {})
    tool_calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        name = str(function.get("name") or "").strip()
        if name:
            tool_calls.append(NormalizedToolCall(name=name, arguments=_loads(function.get("arguments"))))
    usage = _usage(payload.get("usage") or {}, config.model, messages, message.get("content") or "")
    return LLMResponse(config.provider, config.model, str(message.get("content") or "").strip(), tool_calls, usage, payload)


def _normalize_anthropic_response(config: LLMProviderConfig, payload: dict, messages: list[dict]) -> LLMResponse:
    content_parts = payload.get("content") or []
    text = "\n".join(str(part.get("text") or "") for part in content_parts if part.get("type") == "text").strip()
    tool_calls = [
        NormalizedToolCall(name=str(part.get("name") or ""), arguments=part.get("input") if isinstance(part.get("input"), dict) else {})
        for part in content_parts
        if part.get("type") == "tool_use" and part.get("name")
    ]
    raw_usage = payload.get("usage") or {}
    usage = _usage(
        {"prompt_tokens": raw_usage.get("input_tokens"), "completion_tokens": raw_usage.get("output_tokens")},
        config.model,
        messages,
        text,
    )
    return LLMResponse(config.provider, config.model, text, tool_calls, usage, payload)


def _normalize_gemini_response(config: LLMProviderConfig, payload: dict, messages: list[dict]) -> LLMResponse:
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "\n".join(str(part.get("text") or "") for part in parts if part.get("text")).strip()
    tool_calls = []
    for part in parts:
        call = part.get("functionCall") or {}
        if call.get("name"):
            tool_calls.append(NormalizedToolCall(str(call.get("name")), call.get("args") if isinstance(call.get("args"), dict) else {}))
    usage = _usage(payload.get("usageMetadata") or {}, config.model, messages, text)
    return LLMResponse(config.provider, config.model, text, tool_calls, usage, payload)


def _usage(raw: dict, model: str, messages: list[dict], content: str) -> LLMUsage:
    prompt_tokens = int(raw.get("prompt_tokens") or raw.get("input_tokens") or raw.get("promptTokenCount") or _estimate_tokens(json.dumps(messages, default=str)))
    completion_tokens = int(raw.get("completion_tokens") or raw.get("output_tokens") or raw.get("candidatesTokenCount") or _estimate_tokens(content))
    total = int(raw.get("total_tokens") or raw.get("totalTokenCount") or prompt_tokens + completion_tokens)
    cost = round((total / 1000) * MODEL_COST_PER_1K.get(model, 0.001), 6)
    return LLMUsage(prompt_tokens, completion_tokens, total, cost)


def _log_llm_call(db: Session | None, tenant_id: str, purpose: str, config: LLMProviderConfig, response: LLMResponse, latency_ms: int, fallback_used: bool) -> None:
    if db is None:
        return
    db.add(
        AgentAction(
            tenant_id=tenant_id,
            action_type="llm_call",
            status="success",
            payload=json.dumps({"purpose": purpose, "provider": config.provider, "model": config.model, "fallback_used": fallback_used}, ensure_ascii=True),
            result=json.dumps({"usage": response.usage.__dict__, "latency_ms": latency_ms, "tool_calls": [call.__dict__ for call in response.tool_calls]}, ensure_ascii=True, default=str)[:5000],
        )
    )
    db.commit()


def _log_llm_failure(db: Session | None, tenant_id: str, purpose: str, config: LLMProviderConfig, exc: Exception) -> None:
    if db is None:
        return
    db.add(
        AgentAction(
            tenant_id=tenant_id,
            action_type="llm_call",
            status="failed",
            payload=json.dumps({"purpose": purpose, "provider": config.provider, "model": config.model}, ensure_ascii=True),
            result=json.dumps({"error": str(exc)}, ensure_ascii=True)[:5000],
        )
    )
    db.commit()


def _tenant_metadata(db: Session | None, tenant_id: str) -> dict:
    if db is None:
        return {}
    row = get_tenant_config(db, tenant_id)
    if not row:
        return {}
    return serialize_tenant_config(row).get("metadata") or {}


def _dedupe_enabled(candidates: list[LLMProviderConfig]) -> list[LLMProviderConfig]:
    seen = set()
    output = []
    for item in candidates:
        key = (item.provider, item.model, item.base_url)
        if not item.enabled or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _api_key(config: LLMProviderConfig, env_name: str, fallback: str) -> str:
    if config.api_key_env:
        return os.getenv(config.api_key_env, "")
    return os.getenv(env_name, fallback or "")


def _openai_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.APP_URL,
        "X-Title": settings.APP_NAME,
    }


def _timeout() -> int:
    return max(5, int(settings.OPENROUTER_TIMEOUT_SECONDS or DEFAULT_TIMEOUT))


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system = "\n\n".join(str(item.get("content") or "") for item in messages if item.get("role") == "system")
    rows = [
        {"role": "assistant" if item.get("role") == "assistant" else "user", "content": str(item.get("content") or "")}
        for item in messages
        if item.get("role") != "system"
    ]
    return system, rows


def _anthropic_tool(tool: dict) -> dict:
    function = tool.get("function") or {}
    return {"name": function.get("name"), "description": function.get("description") or "", "input_schema": function.get("parameters") or {"type": "object"}}


def _gemini_contents(messages: list[dict]) -> list[dict]:
    return [
        {
            "role": "model" if item.get("role") == "assistant" else "user",
            "parts": [{"text": str(item.get("content") or "")}],
        }
        for item in messages
    ]


def _gemini_tool(tool: dict) -> dict:
    function = tool.get("function") or {}
    return {
        "name": function.get("name"),
        "description": function.get("description") or "",
        "parameters": function.get("parameters") or {"type": "object"},
    }


def _loads(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)
