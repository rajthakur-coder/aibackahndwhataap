from datetime import datetime, timezone

from app.modules.automation.events.event_service import _db_naive, _utcnow_like
from app.modules.automation.runtime import sync_service


def test_automation_event_time_helpers_match_datetime_awareness():
    aware = datetime.now(timezone.utc)
    naive = datetime.utcnow()

    assert _utcnow_like(aware).tzinfo is not None
    assert _utcnow_like(naive).tzinfo is None
    assert _db_naive(aware).tzinfo is None


def test_sync_service_exports_template_button_parameters():
    assert callable(sync_service._template_button_parameters)
