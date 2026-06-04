from sqlalchemy.orm import Session

from app.modules.ai.chat.openai_chat_service import generate_ai_reply
from app.modules.ai.orchestrator.guardrails import harden_reply
from app.modules.ai.orchestrator.prompt_builder import fallback_reply, tool_context_text
from app.modules.ai.orchestrator.response_schema import OrchestratorResponse, ToolCallResult
from app.modules.ai.orchestrator.tool_choice_service import choose_tool_with_llm
from app.modules.ai.orchestrator.tool_executor import execute_tool
from app.modules.ai.orchestrator.tool_registry import normalize_tool_name
from app.modules.ai.understanding.query_understanding_service import understand_message
from app.modules.tenants.tenant_service import tenant_config_context
from app.shared.tenant import DEFAULT_TENANT_ID, normalize_tenant_id


def orchestrate_message(
    db: Session,
    *,
    phone: str,
    message: str,
    tenant_id: str = DEFAULT_TENANT_ID,
    understanding=None,
) -> OrchestratorResponse:
    tenant_id = normalize_tenant_id(tenant_id)
    understanding = understanding or understand_message(message)
    selected_tool = _select_tool(
        understanding.tool,
        understanding.intent,
        understanding.confidence,
        understanding.normalized_query or message,
    )
    entities = dict(understanding.entities or {})
    llm_choice = _structured_tool_choice(
        db,
        tenant_id=tenant_id,
        message=understanding.normalized_query or message,
        selected_tool=selected_tool,
        entities=entities,
        confidence=understanding.confidence,
    )
    if llm_choice:
        selected_tool = llm_choice.name
        entities = llm_choice.arguments
    tool_result = _run_selected_tool(
        db,
        selected_tool=selected_tool,
        phone=phone,
        message=understanding.normalized_query or message,
        entities=entities,
        tenant_id=tenant_id,
    )

    deterministic_reply = _deterministic_reply(tool_result)
    if deterministic_reply:
        reply = deterministic_reply
        return OrchestratorResponse(
            reply=reply,
            intent=understanding.intent,
            selected_tool=selected_tool,
            confidence=understanding.confidence,
            tool_result=tool_result,
        )

    try:
        if tool_result.status == "needs_confirmation":
            reply = fallback_reply(tool_result)
        else:
            reply = generate_ai_reply(
                db,
                phone,
                message,
                agent_context=_agent_context(db, tenant_id, understanding.intent, understanding.confidence),
                tool_context=tool_context_text(tool_result),
                tenant_id=tenant_id,
            )
    except Exception:
        reply = fallback_reply(tool_result)
    reply = harden_reply(reply, tool_result)

    return OrchestratorResponse(
        reply=reply,
        intent=understanding.intent,
        selected_tool=selected_tool,
        confidence=understanding.confidence,
        tool_result=tool_result,
    )


def _structured_tool_choice(
    db: Session,
    *,
    tenant_id: str,
    message: str,
    selected_tool: str,
    entities: dict,
    confidence: float,
):
    if selected_tool == "general_reply" or confidence >= 0.9:
        return None
    return choose_tool_with_llm(
        db,
        tenant_id=tenant_id,
        message=message,
        fallback_tool=selected_tool,
        fallback_entities=entities,
    )


def _select_tool(tool_name: str, intent: str, confidence: float, message: str = "") -> str:
    if confidence < 0.35:
        return "create_support_ticket"
    commerce_tool = _commerce_action_tool(message)
    if commerce_tool:
        return commerce_tool
    if intent in {"greeting", "menu_request"} or tool_name == "general_reply":
        return "general_reply"
    if intent in {"human_handoff", "support_request"}:
        return "create_support_ticket"
    if intent in {"top_selling_products", "catalog_request", "image_request", "price_question"}:
        return "search_catalog"
    if intent in {"order_status", "tracking_question"}:
        return "get_order_status"
    if intent in {"policy_question", "faq_question"}:
        return "get_policy"
    return normalize_tool_name(tool_name)


def _commerce_action_tool(message: str) -> str | None:
    lowered = (message or "").lower()
    if any(term in lowered for term in ("bulk", "gifting", "corporate gift", "b2b", "wedding gift", "hospitality")):
        return "log_bulk_lead"
    if any(term in lowered for term in ("discount", "coupon", "promo code", "apply code")):
        return "apply_discount"
    if "return" in lowered or "exchange" in lowered:
        if any(term in lowered for term in ("policy", "terms", "rule", "rules")):
            return None
        if any(term in lowered for term in ("initiate", "start", "process", "pickup", "refund")):
            return "initiate_return"
        return "get_return_eligibility"
    if any(term in lowered for term in ("tracking link", "track link", "live tracking")):
        return "get_tracking_link"
    if any(term in lowered for term in ("dispatch", "shipped", "shipment details", "awb")):
        return "get_dispatch_details"
    return None


def _agent_context(db: Session, tenant_id: str, intent: str, confidence: float) -> str:
    tenant_context = ""
    if db is not None:
        tenant_context = tenant_config_context(db, tenant_id)
    parts = [
        f"Detected intent: {intent}. Confidence: {confidence:.2f}.",
        tenant_context,
    ]
    return "\n\n".join(part for part in parts if part.strip())


def _run_selected_tool(
    db: Session,
    *,
    selected_tool: str,
    phone: str,
    message: str,
    entities: dict,
    tenant_id: str,
) -> ToolCallResult:
    if selected_tool == "general_reply":
        return ToolCallResult(
            "general_reply",
            "skipped",
            "No structured tool was needed for this turn.",
            {},
        )
    return execute_tool(
        db,
        selected_tool,
        phone=phone,
        message=message,
        entities=entities,
        tenant_id=tenant_id,
    )


def _deterministic_reply(tool_result: ToolCallResult) -> str | None:
    if tool_result.tool_name != "get_order_status" or tool_result.status != "success":
        return None
    if not isinstance(tool_result.data, dict):
        return None

    order_number = tool_result.data.get("order_number") or tool_result.data.get("id") or "your order"
    status = (
        tool_result.data.get("fulfillment_status")
        or tool_result.data.get("shipment_status")
        or tool_result.data.get("delivery_status")
        or tool_result.data.get("status")
        or "received"
    )
    parts = [f"Your order {order_number} status is {status}."]
    if tool_result.data.get("tracking_number"):
        parts.append(f"Tracking number: {tool_result.data['tracking_number']}.")
    if tool_result.data.get("tracking_url"):
        parts.append(f"Track here: {tool_result.data['tracking_url']}")
    total = tool_result.data.get("total")
    currency = tool_result.data.get("currency")
    if total:
        parts.append(f"Total: {total}{f' {currency}' if currency else ''}")
    return " ".join(parts).strip()
