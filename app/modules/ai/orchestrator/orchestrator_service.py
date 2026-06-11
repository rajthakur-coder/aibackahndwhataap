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
    if understanding.intent == "out_of_scope":
        reply = _out_of_scope_reply(db, tenant_id)
        tool_result = ToolCallResult("out_of_scope", "blocked", reply, {"reason": "outside_business_scope"})
        return OrchestratorResponse(
            reply=reply,
            intent=understanding.intent,
            selected_tool="out_of_scope",
            confidence=understanding.confidence,
            tool_result=tool_result,
        )

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

    deterministic_reply = _deterministic_reply(tool_result, understanding.normalized_query or message)
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
    if intent == "out_of_scope" or tool_name == "out_of_scope":
        return "out_of_scope"
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
    if intent in {"contact_request", "policy_question", "faq_question"}:
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
    if selected_tool == "out_of_scope":
        message = _out_of_scope_reply(db, tenant_id)
        return ToolCallResult("out_of_scope", "blocked", message, {"reason": "outside_business_scope"})
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


def _out_of_scope_reply(db: Session | None, tenant_id: str) -> str:
    return "I can help with products, orders, delivery, returns, and support only."


def _deterministic_reply(tool_result: ToolCallResult, message: str = "") -> str | None:
    if tool_result.tool_name == "get_policy" and isinstance(tool_result.data, dict) and tool_result.data.get("contact_requested"):
        return _contact_details_sentence(tool_result.data.get("contact_details") or {}, message)
    if tool_result.tool_name != "get_order_status" or tool_result.status != "success":
        return None
    if not isinstance(tool_result.data, dict):
        return None

    order_number = tool_result.data.get("order_number") or tool_result.data.get("id") or "your order"
    parts = [_order_status_sentence(tool_result.data, order_number)]
    if tool_result.data.get("tracking_number"):
        parts.append(f"Tracking number: {tool_result.data['tracking_number']}.")
    if tool_result.data.get("tracking_url"):
        parts.append(f"Track here: {tool_result.data['tracking_url']}")
    total = tool_result.data.get("total")
    currency = tool_result.data.get("currency")
    if total:
        parts.append(f"Total: {total}{f' {currency}' if currency else ''}")
    return " ".join(parts).strip()


def _contact_details_sentence(details: dict, message: str = "") -> str:
    email = str(details.get("email") or "").strip()
    phone = str(details.get("phone") or "").strip()
    normalized = (message or "").lower()
    wants_phone = any(term in normalized for term in ("mobile", "phone", "number", "contact number", "call", "whatsapp"))
    wants_email = any(term in normalized for term in ("email", "mail", "email id", "emailid"))
    if email and phone:
        return f"You can contact us at {email} or call/WhatsApp {phone}."
    if wants_phone and not phone and email:
        return f"I do not have a customer care mobile number saved for this business yet. You can contact us at {email}."
    if wants_email and not email and phone:
        return f"I do not have a customer care email saved for this business yet. You can call or WhatsApp us at {phone}."
    if email:
        return f"You can contact us at {email}."
    if phone:
        return f"You can call or WhatsApp us at {phone}."
    return "I do not have a customer care email or mobile number saved for this business yet."


def _order_status_sentence(data: dict, order_number: str) -> str:
    status = _order_status_label(data)
    if status:
        return f"Your order {order_number} status is {status}."
    financial_status = str(data.get("financial_status") or "").strip()
    if financial_status:
        return f"Your order {order_number} payment status is {financial_status}. Fulfillment status is not available yet."
    return f"I could not confirm the latest status for order {order_number} right now."


def _order_status_label(data: dict) -> str | None:
    status_values = {
        str(data.get("status") or "").strip().lower(),
        str(data.get("fulfillment_status") or "").strip().lower(),
        str(data.get("financial_status") or "").strip().lower(),
    }
    if data.get("cancelled_at") or data.get("cancel_reason") or status_values & {"cancelled", "canceled", "voided"}:
        return "cancelled"
    return (
        data.get("delivery_status")
        or data.get("shipment_status")
        or data.get("fulfillment_status")
        or data.get("status")
    )
