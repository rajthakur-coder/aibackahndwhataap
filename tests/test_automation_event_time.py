from datetime import datetime, timezone

from app.modules.automation.events.event_service import _db_naive, _utcnow_like


def test_automation_event_time_helpers_match_datetime_awareness():
    aware = datetime.now(timezone.utc)
    naive = datetime.utcnow()

    assert _utcnow_like(aware).tzinfo is not None
    assert _utcnow_like(naive).tzinfo is None
    assert _db_naive(aware).tzinfo is None
