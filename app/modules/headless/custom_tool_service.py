import json

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compliance import TenantCustomTool
from app.models.crm import AgentAction
from app.shared.tenant import normalize_tenant_id


REQUEST_TIMEOUT = 20


def list_custom_tools(db: Session, tenant_id: str) -> list[dict]:
    rows = db.execute(select(TenantCustomTool).where(TenantCustomTool.tenant_id == normalize_tenant_id(tenant_id))).scalars().all()
    return [serialize_custom_tool(row) for row in rows]


def get_custom_tool(db: Session, tenant_id: str, name: str) -> TenantCustomTool | None:
    return db.execute(
        select(TenantCustomTool).where(
            TenantCustomTool.tenant_id == normalize_tenant_id(tenant_id),
            TenantCustomTool.name == str(name or "").strip(),
            TenantCustomTool.status == "active",
        )
    ).scalars().first()


def upsert_custom_tool(db: Session, tenant_id: str, payload: dict) -> dict:
    tenant_id = normalize_tenant_id(tenant_id)
    name = str(payload.get("name") or "").strip()
    row = db.execute(select(TenantCustomTool).where(TenantCustomTool.tenant_id == tenant_id, TenantCustomTool.name == name)).scalars().first()
    if not row:
        row = TenantCustomTool(tenant_id=tenant_id, name=name)
        db.add(row)
    row.description = payload.get("description")
    row.input_schema = json.dumps(payload.get("input_schema") or {}, ensure_ascii=True)
    row.endpoint_url = payload.get("endpoint_url")
    row.status = payload.get("status") or "active"
    row.fallback = payload.get("fallback") or "create_support_ticket"
    db.commit()
    db.refresh(row)
    return serialize_custom_tool(row)


def execute_custom_tool(
    db: Session,
    tenant_id: str,
    name: str,
    *,
    phone: str,
    message: str,
    entities: dict,
) -> dict:
    row = get_custom_tool(db, tenant_id, name)
    if not row:
        raise ValueError(f"Custom tool not found: {name}")
    payload = {
        "tenant_id": normalize_tenant_id(tenant_id),
        "tool": row.name,
        "phone": phone,
        "message": message,
        "arguments": entities or {},
    }
    if not row.endpoint_url:
        result = {"status": "configured", "message": "Custom tool has no endpoint URL.", "payload": payload}
        _log_custom_tool(db, tenant_id, phone, row.name, "skipped", payload, result)
        return result
    try:
        response = requests.post(row.endpoint_url, json=payload, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            result = {"text": response.text[:4000]}
        _log_custom_tool(db, tenant_id, phone, row.name, "success", payload, result)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        result = {"error": str(exc), "fallback": row.fallback or "create_support_ticket"}
        _log_custom_tool(db, tenant_id, phone, row.name, "failed", payload, result)
        raise


def serialize_custom_tool(row: TenantCustomTool) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "description": row.description,
        "input_schema": _loads(row.input_schema),
        "endpoint_url": row.endpoint_url,
        "status": row.status,
        "fallback": row.fallback,
    }


def custom_tool_json_schema(row: TenantCustomTool) -> dict:
    schema = _loads(row.input_schema)
    properties = {}
    required = []
    for key, value in schema.items():
        properties[key] = _json_schema_value(value)
        if "null" not in str(value):
            required.append(key)
    return {
        "type": "function",
        "function": {
            "name": row.name,
            "description": row.description or f"Tenant custom tool {row.name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _log_custom_tool(db: Session, tenant_id: str, phone: str, name: str, status: str, payload: dict, result: dict) -> None:
    db.add(
        AgentAction(
            tenant_id=normalize_tenant_id(tenant_id),
            phone=phone,
            action_type="custom_tool_executed",
            status=status,
            payload=json.dumps(payload, ensure_ascii=True, default=str),
            result=json.dumps({"tool": name, **(result or {})}, ensure_ascii=True, default=str)[:5000],
        )
    )
    db.commit()


def _json_schema_value(value) -> dict:
    text = str(value or "string").lower()
    if "integer" in text or text == "int":
        return {"type": "integer"}
    if "number" in text:
        return {"type": "number"}
    if "array" in text or "list" in text:
        return {"type": "array", "items": {"type": "string"}}
    if "boolean" in text or text == "bool":
        return {"type": "boolean"}
    if "null" in text:
        return {"type": ["string", "null"]}
    return {"type": "string"}


def _loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
