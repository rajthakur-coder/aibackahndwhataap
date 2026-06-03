from app.modules.ai.orchestrator.tool_executor import _extract_order_id
from app.modules.ai.understanding.query_understanding_service import understand_message


def test_bare_order_number_routes_to_order_status():
    result = understand_message("1234")

    assert result.intent == "order_status"
    assert result.tool == "get_order_status"
    assert result.entities["order_id"] == "1234"


def test_order_id_extractor_accepts_bare_values():
    assert _extract_order_id("1234") == "1234"
    assert _extract_order_id("#HS-1") == "HS-1"
    assert _extract_order_id("track order #1234") == "1234"
