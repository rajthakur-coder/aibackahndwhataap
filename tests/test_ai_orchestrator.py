from app.modules.ai.orchestrator.orchestrator_service import _select_tool
from app.modules.ai.orchestrator.orchestrator_service import orchestrate_message
from app.modules.ai.orchestrator.prompt_builder import fallback_reply, tool_context_text
from app.modules.ai.orchestrator.response_schema import ToolCallResult
from app.modules.ai.orchestrator.tool_registry import get_tool, list_tools, normalize_tool_name
from app.modules.ai.understanding.query_understanding_service import QueryUnderstanding, understand_message


def test_registry_exposes_phase_one_tools():
    names = {tool.name for tool in list_tools()}

    assert {
        "get_order_status",
        "search_catalog",
        "get_product",
        "get_policy",
        "get_bundle_recommendations",
        "create_support_ticket",
    }.issubset(names)


def test_legacy_tool_aliases_normalize_to_phase_one_tools():
    assert normalize_tool_name("search_products") == "search_catalog"
    assert normalize_tool_name("get_policy_or_faq") == "get_policy"
    assert get_tool("search_products").name == "search_catalog"


def test_greeting_uses_no_structured_tool():
    assert _select_tool("general_reply", "menu_request", 0.8) == "general_reply"
    assert _select_tool("general_reply", "greeting", 0.8) == "general_reply"


def test_commerce_action_messages_select_commerce_tools():
    assert _select_tool("search_knowledge", "general", 0.8, "return eligibility for order #HS-1") == "get_return_eligibility"
    assert _select_tool("search_knowledge", "general", 0.8, "bulk gifting for 100 people") == "log_bulk_lead"
    assert _select_tool("search_knowledge", "general", 0.8, "apply discount FIRST10") == "apply_discount"
    assert _select_tool("search_knowledge", "general", 0.8, "send live tracking link") == "get_tracking_link"


def test_coding_questions_are_out_of_scope():
    javascript = understand_message("what is javascript")
    nextjs = understand_message("how to create a new next js app")

    assert javascript.intent == "out_of_scope"
    assert javascript.tool == "out_of_scope"
    assert nextjs.intent == "out_of_scope"
    assert nextjs.tool == "out_of_scope"


def test_tool_context_and_fallback_are_deterministic():
    result = ToolCallResult(
        "search_catalog",
        "success",
        "Found 1 matching product.",
        {"items": [{"title": "Linen Throw", "price": "1999"}]},
    )

    context = tool_context_text(result)

    assert "Tool used: search_catalog" in context
    assert "Linen Throw" in context
    assert fallback_reply(result) == "Found 1 matching product."


def test_orchestrator_can_use_existing_webhook_understanding(monkeypatch):
    understanding = QueryUnderstanding(
        original_message="hi",
        normalized_query="hi",
        intent="greeting",
        confidence=0.9,
        tool="general_reply",
    )

    monkeypatch.setattr(
        "app.modules.ai.orchestrator.orchestrator_service.generate_ai_reply",
        lambda *args, **kwargs: "Hello",
    )

    response = orchestrate_message(
        None,
        phone="919999999999",
        message="hi",
        tenant_id="brand-a",
        understanding=understanding,
    )

    assert response.reply == "Hello"
    assert response.selected_tool == "general_reply"
    assert response.tool_result.status == "skipped"


def test_orchestrator_blocks_out_of_scope_without_llm(monkeypatch):
    understanding = QueryUnderstanding(
        original_message="what is javascript",
        normalized_query="what is javascript",
        intent="out_of_scope",
        confidence=0.95,
        tool="out_of_scope",
    )

    monkeypatch.setattr(
        "app.modules.ai.orchestrator.orchestrator_service.generate_ai_reply",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    response = orchestrate_message(
        None,
        phone="919999999999",
        message="what is javascript",
        tenant_id="brand-a",
        understanding=understanding,
    )

    assert response.selected_tool == "out_of_scope"
    assert response.tool_result.status == "blocked"
    assert response.reply == "I can help with products, orders, delivery, returns, and support only."
