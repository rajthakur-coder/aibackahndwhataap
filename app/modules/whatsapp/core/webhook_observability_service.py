import json
import time
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.models.crm import AgentAction


class WebhookTiming:
    def __init__(self, db: Session, phone: str, event_id: int | None = None) -> None:
        self.db = db
        self.phone = phone
        self.event_id = event_id
        self.started_at = time.perf_counter()
        self.timings_ms: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            self.timings_ms[name] = self.timings_ms.get(name, 0.0) + elapsed_ms

    def log(self, status: str = "processed", extra: dict | None = None) -> None:
        total_ms = (time.perf_counter() - self.started_at) * 1000
        payload = {
            "event_id": self.event_id,
            "total_ms": round(total_ms, 2),
            "timings_ms": {
                key: round(value, 2)
                for key, value in sorted(self.timings_ms.items())
            },
        }
        if extra:
            payload.update(extra)
        self.db.add(
            AgentAction(
                phone=self.phone,
                action_type="webhook_stage_timing",
                status=status,
                payload=json.dumps(payload, ensure_ascii=True),
            )
        )
        self.db.commit()
