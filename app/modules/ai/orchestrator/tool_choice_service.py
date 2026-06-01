import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.ai.orchestrator.tool_registry import list_tools, tool_json_schema
from app.modules.headless.custom_tool_service import custom_tool_json_schema
from app.modules.headless.llm_provider import chat_completion, normalize_tool_choice_response
from app.modules.tenants.tenant_service import tenant_config_context
from app.shared.tenant import normalize_tenant_id


@dataclass(frozen=True)
class ToolChoice:
    name: str
    arguments: dict
    source: str = "llm_tool_choice"


def choose_tool_with_llm(
    db: Session,
    *,
    tenant_id: str,
    message: str,
    fallback_tool: str,
    fallback_entities: dict | None = None,
) -> ToolChoice | None:
    if db is None:
        return None
    tools = _tools_for_tenant(db, tenant_id)
    if not tools:
        return None

    system = (
        "You route WhatsApp commerce messages to tools. "
        "Choose a tool only when it is clearly useful. "
        "Never choose destructive tools unless the user explicitly requested that action. "
        "Do not invent order IDs, SKUs, prices, policies, or delivery dates."
    )
    tenant_context = tenant_config_context(db, tenant_id)
    if tenant_context:
        system += f"\n\nTenant context:\n{tenant_context[:2500]}"

    try:
        response = chat_completion(
            db,
            tenant_id=tenant_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            tools=tools,
            purpose="tool_choice",
            temperature=0,
            max_tokens=120,
        )
        normalized = normalize_tool_choice_response(response)
        choice = ToolChoice(normalized[0], normalized[1]) if normalized else _extract_tool_choice(response.raw)
    except Exception:
        return None
    if not choice:
        return None
    args = {**(fallback_entities or {}), **choice.arguments}
    if choice.name == fallback_tool and not choice.arguments:
        args = fallback_entities or {}
    return ToolChoice(choice.name, args)


def _tools_for_tenant(db: Session, tenant_id: str) -> list[dict]:
    core = [tool_json_schema(tool) for tool in list_tools()]
    try:
        custom = [custom_tool_json_schema(row) for row in _custom_tool_rows(db, tenant_id)]
    except Exception:
        custom = []
    return core + custom


def _custom_tool_rows(db: Session, tenant_id: str):
    from app.models.compliance import TenantCustomTool
    from sqlalchemy import select

    return db.execute(
        select(TenantCustomTool).where(
            TenantCustomTool.tenant_id == normalize_tenant_id(tenant_id),
            TenantCustomTool.status == "active",
        )
    ).scalars().all()


def _extract_tool_choice(payload: dict) -> ToolChoice | None:
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = (choices[0] or {}).get("message") or {}
    calls = message.get("tool_calls") or []
    if calls:
        function = (calls[0] or {}).get("function") or {}
        name = str(function.get("name") or "").strip()
        arguments = _loads(function.get("arguments"))
        return ToolChoice(name, arguments) if name else None
    content = str(message.get("content") or "")
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        return None
    data = _loads(match.group(0))
    name = str(data.get("tool") or data.get("name") or "").strip()
    arguments = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
    return ToolChoice(name, arguments) if name else None


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
