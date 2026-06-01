import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.crm import AgentAction
from app.shared.tenant import normalize_tenant_id


PENDING_ACTION = "pending_destructive_tool_confirmation"
CONFIRMED_ACTION = "destructive_tool_confirmed"
CANCELLED_ACTION = "destructive_tool_cancelled"


def needs_confirmation(tool_name: str, entities: dict) -> bool:
    if entities.get("confirmed") is True:
        return False
    if entities.get("confirmation_id"):
        return False
    return True


def create_confirmation(
    db: Session,
    *,
    tenant_id: str,
    phone: str,
    tool_name: str,
    message: str,
    entities: dict,
    summary: str | None = None,
) -> dict:
    payload = {
        "tenant_id": normalize_tenant_id(tenant_id),
        "tool_name": tool_name,
        "message": message,
        "entities": entities,
        "summary": summary or _default_summary(tool_name, entities),
    }
    action = AgentAction(
        phone=phone,
        action_type=PENDING_ACTION,
        status="pending",
        payload=json.dumps(payload, ensure_ascii=True, default=str),
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return {
        "confirmation_id": action.id,
        "tool_name": tool_name,
        "summary": payload["summary"],
        "buttons": [
            {"id": f"confirm_tool:{action.id}:yes", "title": "Yes"},
            {"id": f"confirm_tool:{action.id}:no", "title": "No"},
        ],
    }


def consume_confirmation(db: Session, *, confirmation_id, phone: str, tenant_id: str) -> dict | None:
    try:
        parsed_id = int(confirmation_id)
    except (TypeError, ValueError):
        return None
    action = db.execute(
        select(AgentAction)
        .where(
            AgentAction.id == parsed_id,
            AgentAction.phone == phone,
            AgentAction.action_type == PENDING_ACTION,
            AgentAction.status == "pending",
        )
        .limit(1)
    ).scalars().first()
    if not action:
        return None
    payload = _loads(action.payload)
    if normalize_tenant_id(payload.get("tenant_id")) != normalize_tenant_id(tenant_id):
        return None
    action.status = "confirmed"
    action.action_type = CONFIRMED_ACTION
    db.commit()
    return payload


def cancel_confirmation(db: Session, *, confirmation_id, phone: str, tenant_id: str) -> bool:
    try:
        parsed_id = int(confirmation_id)
    except (TypeError, ValueError):
        return False
    action = db.execute(
        select(AgentAction)
        .where(
            AgentAction.id == parsed_id,
            AgentAction.phone == phone,
            AgentAction.action_type == PENDING_ACTION,
            AgentAction.status == "pending",
        )
        .limit(1)
    ).scalars().first()
    if not action:
        return False
    payload = _loads(action.payload)
    if normalize_tenant_id(payload.get("tenant_id")) != normalize_tenant_id(tenant_id):
        return False
    action.status = "cancelled"
    action.action_type = CANCELLED_ACTION
    db.commit()
    return True


def confirmation_from_message(message: str) -> tuple[str | None, bool | None]:
    parts = (message or "").strip().split(":")
    if len(parts) == 3 and parts[0] == "confirm_tool":
        return parts[1], parts[2].lower() == "yes"
    return None, None


def _default_summary(tool_name: str, entities: dict) -> str:
    if tool_name == "initiate_return":
        order_id = entities.get("order_id") or "this order"
        reason = entities.get("reason") or "customer requested return"
        return f"Start a return request for {order_id}. Reason: {reason}."
    return f"Run {tool_name} with the provided details."


def _loads(value: str | None) -> dict:
    if not value:
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
