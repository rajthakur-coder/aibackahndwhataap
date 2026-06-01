from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.crm import AgentAction
from app.models.tenants import TenantConfig
from app.modules.headless.llm_provider import chat_completion, tenant_llm_candidates


class _Response:
    def __init__(self, payload, fail=False):
        self.payload = payload
        self.fail = fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("provider down")

    def json(self):
        return self.payload


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    TenantConfig.__table__.create(bind=engine)
    AgentAction.__table__.create(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def test_tenant_llm_candidates_use_tenant_metadata():
    db = _session()
    db.add(
        TenantConfig(
            tenant_id="brand-a",
            brand_name="Brand A",
            metadata_json='{"llm":{"provider":"openrouter","model":"openai/gpt-4.1","fallbacks":[{"provider":"openrouter","model":"openai/gpt-4o-mini"}]}}',
        )
    )
    db.commit()

    candidates = tenant_llm_candidates(db, "brand-a")

    assert [(item.provider, item.model) for item in candidates[:2]] == [
        ("openrouter", "openai/gpt-4.1"),
        ("openrouter", "openai/gpt-4o-mini"),
    ]


def test_chat_completion_falls_back_and_logs_usage(monkeypatch):
    db = _session()
    db.add(
        TenantConfig(
            tenant_id="brand-a",
            brand_name="Brand A",
            metadata_json='{"llm":{"provider":"openrouter","model":"primary-model","fallbacks":[{"provider":"openrouter","model":"fallback-model"}]}}',
        )
    )
    db.commit()
    calls = []
    monkeypatch.setattr("app.modules.headless.llm_provider.settings.OPENROUTER_API_KEY", "test-key")

    def fake_post(url, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "primary-model":
            return _Response({}, fail=True)
        return _Response(
            {
                "choices": [{"message": {"content": "Hello from fallback"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            }
        )

    monkeypatch.setattr("app.modules.headless.llm_provider.requests.post", fake_post)

    response = chat_completion(
        db,
        tenant_id="brand-a",
        messages=[{"role": "user", "content": "hi"}],
        purpose="reply",
    )

    logs = db.query(AgentAction).order_by(AgentAction.id).all()
    assert calls == ["primary-model", "fallback-model"]
    assert response.content == "Hello from fallback"
    assert response.fallback_used is True
    assert logs[0].status == "failed"
    assert logs[1].tenant_id == "brand-a"
    assert logs[1].status == "success"
