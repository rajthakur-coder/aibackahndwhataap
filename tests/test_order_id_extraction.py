from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.modules.ai.orchestrator.tool_executor import _extract_order_id
from app.modules.ai.understanding.query_understanding_service import understand_message
from app.models.whatsapp import Message
from app.modules.whatsapp.webhooks.flows.commerce_flows import (
    _extract_return_order_id,
    _is_manual_return_order_id,
    _is_manual_track_order_id,
    _is_return_request,
    _is_track_request,
    _is_welcome_request,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Message.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_bare_order_number_routes_to_order_status():
    result = understand_message("1234")

    assert result.intent == "order_status"
    assert result.tool == "get_order_status"
    assert result.entities["order_id"] == "1234"


def test_casual_single_word_is_not_bare_order_id():
    result = understand_message("yoo")

    assert result.intent != "order_status"
    assert result.tool != "get_order_status"
    assert "order_id" not in result.entities
    assert _extract_order_id("yoo") is None


def test_random_alphabet_words_do_not_trigger_order_lookup():
    casual_messages = [
        "yo",
        "yoo",
        "bro",
        "test",
        "random",
        "checking",
        "hmmm",
        "okay",
        "thanks",
        "nice",
    ]

    for message in casual_messages:
        result = understand_message(message)

        assert result.intent != "order_status", message
        assert result.tool != "get_order_status", message
        assert "order_id" not in result.entities, message
        assert _extract_order_id(message) is None, message


def test_low_information_messages_use_rules_without_llm(monkeypatch):
    def fail_llm(*args, **kwargs):
        raise AssertionError("Low-information messages should not call the LLM router")

    monkeypatch.setattr(
        "app.modules.ai.understanding.query_understanding_service.chat_completion",
        fail_llm,
    )

    for message in ("yoo", "test", "okay", "thanks"):
        result = understand_message(message)

        assert result.intent == "menu_request", message
        assert result.tool == "general_reply", message
        assert _is_welcome_request(message) is True


def test_order_id_extractor_accepts_bare_values():
    assert _extract_order_id("1234") == "1234"
    assert _extract_order_id("ORD1234") == "ORD1234"
    assert _extract_order_id("#HS-1") == "HS-1"
    assert _extract_order_id("track order #1234") == "1234"


def test_track_request_accepts_order_id_in_same_message():
    assert _is_track_request("track order #5968") is True
    assert _is_track_request("order status #5968") is True


def test_return_policy_does_not_become_return_order_id():
    assert _extract_return_order_id("return policy kya ha") is None
    assert _extract_return_order_id("exchange policy") is None
    assert _is_return_request("return policy kya ha") is False
    assert _is_return_request("exchange policy") is False


def test_bare_order_id_after_track_prompt_is_not_return_order_id():
    db = _session()
    phone = "919999999999"
    db.add(Message(tenant_id="brand-a", phone=phone, message="Return", direction="incoming"))
    db.add(
        Message(
            tenant_id="brand-a",
            phone=phone,
            message="Sure. Drop your order ID, like #1234, or the phone used for the order.",
            direction="outgoing",
        )
    )
    db.commit()
    context = SimpleNamespace(db=db, tenant_id="brand-a", phone=phone)

    assert _is_manual_track_order_id(context, "#5967") is True
    assert _is_manual_return_order_id(context, "#5967") is False


def test_bare_order_id_after_return_prompt_still_continues_return():
    db = _session()
    phone = "919999999999"
    db.add(Message(tenant_id="brand-a", phone=phone, message="Return", direction="incoming"))
    db.add(
        Message(
            tenant_id="brand-a",
            phone=phone,
            message="I could not find a recent order on this WhatsApp number. Please share your order ID, like #1234.",
            direction="outgoing",
        )
    )
    db.commit()
    context = SimpleNamespace(db=db, tenant_id="brand-a", phone=phone)

    assert _is_manual_track_order_id(context, "#5967") is False
    assert _is_manual_return_order_id(context, "#5967") is True
