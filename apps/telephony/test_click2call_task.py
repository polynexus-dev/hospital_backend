"""M1: click-to-call runs on a Celery worker (place_click_to_call), not a
per-request daemon thread, and logs instead of print()ing."""
import datetime

import pytest
from django.utils import timezone

from apps.telephony import adapters, tasks
from apps.telephony.models import Call


class _StubProvider:
    def __init__(self, uuid=None, boom=False):
        self._uuid, self._boom = uuid, boom

    def initiate_call(self, *, from_number, to_number):
        if self._boom:
            raise RuntimeError("PBX unreachable")
        return self._uuid


def _call(hospital):
    return Call.objects.create(
        hospital=hospital, direction=Call.Direction.OUTBOUND, status=Call.Status.RNR,
        from_number="101", to_number="9876500099", started_at=timezone.now(),
        provider_call_id="pending-101-9876500099",
    )


@pytest.mark.django_db
def test_click2call_success_marks_answered(hospital, monkeypatch):
    monkeypatch.setattr(adapters, "get_telephony_provider", lambda: _StubProvider(uuid="PBX-UUID-1"))
    call = _call(hospital)
    tasks.place_click_to_call(call.id, "101", "9876500099")
    call.refresh_from_db()
    assert call.status == Call.Status.ANSWERED
    assert call.provider_call_id == "PBX-UUID-1"
    assert call.answered_at is not None


@pytest.mark.django_db
def test_click2call_failure_marks_failed(hospital, monkeypatch):
    monkeypatch.setattr(adapters, "get_telephony_provider", lambda: _StubProvider(boom=True))
    call = _call(hospital)
    tasks.place_click_to_call(call.id, "101", "9876500099")  # must not raise
    call.refresh_from_db()
    assert call.status == Call.Status.FAILED
    assert call.provider_call_id == "failed-101-9876500099"
