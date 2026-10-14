"""Doctors' Google / Outlook calendar sync: appointments out, busy times in."""
import datetime
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.appointments.calendar_sync import initials, sync_appointment, sync_busy, sync_connection
from apps.appointments.models import Appointment, CalendarAppointmentEvent, CalendarConnection, Doctor, Slot
from apps.patients.models import Patient

GOOGLE = {"GOOGLE_CALENDAR_CLIENT_ID": "gid", "GOOGLE_CALENDAR_CLIENT_SECRET": "gsecret", "PUBLIC_APP_URL": "https://app.example.in"}


@pytest.fixture
def doctor(hospital, department, user):
    return Doctor.objects.create(hospital=hospital, department=department, name="Dr Mehta", user=user)


@pytest.fixture
def conn(hospital, doctor):
    return CalendarConnection.objects.create(hospital=hospital, doctor=doctor, provider="google", account_email="mehta@gmail.com",
                                             access_token="tok", refresh_token="ref", token_expires_at=timezone.now() + datetime.timedelta(hours=1))


def _slot(hospital, doctor, day_offset, hh, mm=0, minutes=15):
    day = timezone.localdate() + datetime.timedelta(days=day_offset)
    start = datetime.time(hh, mm)
    end = (datetime.datetime.combine(day, start) + datetime.timedelta(minutes=minutes)).time()
    return Slot.objects.create(hospital=hospital, doctor=doctor, date=day, start_time=start, end_time=end)


def _local(day_offset, hh, mm=0):
    day = timezone.localdate() + datetime.timedelta(days=day_offset)
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time(hh, mm)), timezone.get_current_timezone())


def _appointment(hospital, doctor, slot, name="Asha Patil", reason="follow-up"):
    patient = Patient.objects.create(hospital=hospital, first_name=name.split()[0], last_name=name.split()[-1], mobile="9822000001")
    return Appointment.objects.create(hospital=hospital, patient=patient, doctor=doctor, slot=slot, reason=reason, registration_token=f"t{slot.pk}")


def _provider(**kw):
    p = MagicMock()
    p.upsert_event.return_value = "ev-1"
    p.busy_events.return_value = kw.get("busy", [])
    return p


# --- Appointments -> calendar ---------------------------------------------------

def test_initials_never_spell_out_the_name():
    assert initials("Asha  Patil") == "A.P."
    assert initials("") == "Patient"


@pytest.mark.django_db
@override_settings(**GOOGLE)
def test_booking_creates_an_event_with_initials_only_then_cancelling_removes_it(hospital, doctor, conn):
    appointment = _appointment(hospital, doctor, _slot(hospital, doctor, 1, 10))
    provider = _provider()
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        assert sync_appointment(appointment) == "created"
        ev = provider.upsert_event.call_args.args[3]
        assert ev["summary"] == "OPD: A.P. (follow-up)"
        assert "Asha" not in ev["description"] and "Patil" not in ev["description"]
        assert ev["start"] == _local(1, 10) and ev["end"] == _local(1, 10, 15)
        assert "https://app.example.in/appointments?appointment=" in ev["description"]

        appointment.status = Appointment.Status.CANCELLED
        appointment.save()
        assert sync_appointment(appointment) == "deleted"
    provider.delete_event.assert_called_once_with(conn, "tok", "ev-1")
    assert not CalendarAppointmentEvent.objects.exists()


@pytest.mark.django_db
def test_no_calendar_no_sync(hospital, doctor):
    appointment = _appointment(hospital, doctor, _slot(hospital, doctor, 1, 10))
    assert sync_appointment(appointment) == "skipped"


@pytest.mark.django_db(transaction=True)
def test_saving_an_appointment_queues_the_calendar_sync_only_for_connected_doctors(hospital, doctor, department):
    other = Doctor.objects.create(hospital=hospital, department=department, name="Dr Rao")
    with patch("apps.appointments.tasks.sync_appointment_to_calendar.delay") as delay:
        _appointment(hospital, other, _slot(hospital, other, 1, 9))
        assert delay.call_count == 0
        CalendarConnection.objects.create(hospital=hospital, doctor=doctor, provider="google", refresh_token="r")
        appointment = _appointment(hospital, doctor, _slot(hospital, doctor, 1, 9))
    delay.assert_called_once_with(appointment.pk)


@pytest.mark.django_db
def test_expired_access_token_is_refreshed(hospital, doctor, conn):
    conn.token_expires_at = timezone.now() - datetime.timedelta(minutes=1)
    conn.save()
    appointment = _appointment(hospital, doctor, _slot(hospital, doctor, 1, 10))
    provider = _provider()
    provider.refresh.return_value = {"access_token": "new-tok", "expires_in": 3600}
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        sync_appointment(appointment)
    assert provider.upsert_event.call_args.args[1] == "new-tok"
    conn.refresh_from_db()
    assert conn.access_token == "new-tok" and conn.refresh_token == "ref"


# --- Calendar -> slots ---------------------------------------------------------

@pytest.mark.django_db
def test_busy_event_blocks_overlapping_free_slots_and_lifts_them_when_it_goes(hospital, doctor, conn):
    s10, s1015, s1030 = _slot(hospital, doctor, 1, 10), _slot(hospital, doctor, 1, 10, 15), _slot(hospital, doctor, 1, 10, 30)
    provider = _provider(busy=[("g-meeting", _local(1, 10, 5), _local(1, 10, 25))])
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        assert sync_busy(conn) == {"blocked": 2, "unblocked": 0, "clashes": 0}
        for s, blocked in ((s10, True), (s1015, True), (s1030, False)):
            s.refresh_from_db()
            assert s.is_blocked is blocked
        provider.busy_events.return_value = []
        assert sync_busy(conn)["unblocked"] == 2
    s10.refresh_from_db()
    assert not s10.is_blocked and s10.calendar_block is None


@pytest.mark.django_db
def test_calendar_never_lifts_a_block_made_by_hand(hospital, doctor, conn):
    leave = _slot(hospital, doctor, 1, 11)
    Slot.objects.filter(pk=leave.pk).update(is_blocked=True, blocked_reason="On leave")
    with patch("apps.appointments.calendar_sync.provider_for", return_value=_provider(busy=[])):
        sync_busy(conn)
    leave.refresh_from_db()
    assert leave.is_blocked and leave.blocked_reason == "On leave"


@pytest.mark.django_db
def test_busy_over_a_booked_slot_raises_one_task_and_keeps_the_booking(hospital, doctor, conn):
    from apps.automation.models import Task

    slot = _slot(hospital, doctor, 2, 12)
    appointment = _appointment(hospital, doctor, slot)
    provider = _provider(busy=[("g-ot", _local(2, 11), _local(2, 13))])
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        assert sync_busy(conn)["clashes"] == 1
        assert sync_busy(conn)["clashes"] == 0
    task = Task.objects.get()
    assert f"#{appointment.pk}" in task.title and "Asha" not in task.title
    slot.refresh_from_db()
    assert not slot.is_blocked


@pytest.mark.django_db
def test_our_own_appointment_events_are_not_read_back_as_busy(hospital, doctor, conn):
    appointment = _appointment(hospital, doctor, _slot(hospital, doctor, 1, 10))
    CalendarAppointmentEvent.objects.create(hospital=hospital, connection=conn, appointment=appointment, external_event_id="ours")
    free = _slot(hospital, doctor, 1, 10, 15)
    provider = _provider(busy=[("ours", _local(1, 10), _local(1, 10, 30))])
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        sync_busy(conn)
    free.refresh_from_db()
    assert not free.is_blocked


@pytest.mark.django_db
def test_switching_off_blocking_releases_calendar_blocks(hospital, doctor, conn):
    s = _slot(hospital, doctor, 1, 10)
    provider = _provider(busy=[("g1", _local(1, 10), _local(1, 11))])
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        sync_busy(conn)
        conn.block_busy_slots = False
        conn.save()
        sync_busy(conn)
    s.refresh_from_db()
    assert not s.is_blocked


@pytest.mark.django_db
def test_revoked_access_marks_the_connection_for_reconnect(hospital, doctor, conn):
    from apps.appointments.calendar_sync import AccessRevoked

    provider = _provider()
    provider.busy_events.side_effect = AccessRevoked("withdrawn")
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        assert "error" in sync_connection(conn)
    conn.refresh_from_db()
    assert conn.status == "revoked"


@pytest.mark.django_db
def test_full_sync_backfills_upcoming_appointments(hospital, doctor, conn):
    _appointment(hospital, doctor, _slot(hospital, doctor, 3, 9))
    provider = _provider()
    with patch("apps.appointments.calendar_sync.provider_for", return_value=provider):
        result = sync_connection(conn)
    assert result["pushed"] == 1
    assert CalendarAppointmentEvent.objects.count() == 1


# --- Providers -----------------------------------------------------------------

def test_google_busy_parsing_skips_free_declined_and_our_events():
    from apps.appointments.calendar_sync import Google

    items = [
        {"id": "a", "status": "confirmed", "start": {"dateTime": "2026-10-15T10:00:00+05:30"}, "end": {"dateTime": "2026-10-15T11:00:00+05:30"}},
        {"id": "free", "transparency": "transparent", "start": {"dateTime": "2026-10-15T10:00:00Z"}, "end": {"dateTime": "2026-10-15T11:00:00Z"}},
        {"id": "declined", "attendees": [{"self": True, "responseStatus": "declined"}], "start": {"dateTime": "2026-10-15T10:00:00Z"}, "end": {"dateTime": "2026-10-15T11:00:00Z"}},
        {"id": "ours", "extendedProperties": {"private": {"polynexus_appointment": "5"}}, "start": {"dateTime": "2026-10-15T10:00:00Z"}, "end": {"dateTime": "2026-10-15T11:00:00Z"}},
        {"id": "leave", "start": {"date": "2026-10-16"}, "end": {"date": "2026-10-17"}},
    ]
    response = MagicMock(status_code=200, content=b"x")
    response.json.return_value = {"items": items}
    conn = CalendarConnection(calendar_id="primary")
    with patch("requests.get", return_value=response):
        events = Google().busy_events(conn, "tok", timezone.now(), timezone.now())
    assert [e[0] for e in events] == ["a", "leave"]
    assert events[0][1].utcoffset() == datetime.timedelta(hours=5, minutes=30)
    assert (events[1][2] - events[1][1]) == datetime.timedelta(days=1)


def test_microsoft_busy_parsing_keeps_busy_oof_and_tentative():
    from apps.appointments.calendar_sync import Microsoft

    def item(i, show, **kw):
        return {"id": i, "showAs": show, "start": {"dateTime": "2026-10-15T04:30:00.0000000"}, "end": {"dateTime": "2026-10-15T05:30:00.0000000"}, **kw}

    response = MagicMock(status_code=200, content=b"x")
    response.json.return_value = {"value": [item("b", "busy"), item("o", "oof"), item("t", "tentative"), item("f", "free"),
                                            item("c", "busy", isCancelled=True), item("d", "busy", responseStatus={"response": "declined"})]}
    with patch("requests.get", return_value=response):
        events = Microsoft().busy_events(CalendarConnection(), "tok", timezone.now(), timezone.now())
    assert [e[0] for e in events] == ["b", "o", "t"]
    assert events[0][1] == datetime.datetime(2026, 10, 15, 4, 30, tzinfo=datetime.timezone.utc)


# --- Connecting (OAuth) ----------------------------------------------------------

@pytest.mark.django_db
@override_settings(**GOOGLE)
def test_connect_and_callback_store_the_connection(auth_client, api_client, doctor):
    r = auth_client.post("/api/v1/calendar/connect/", {"doctor": doctor.pk, "provider": "google"}, format="json")
    assert r.status_code == 200
    query = parse_qs(urlparse(r.data["url"]).query)
    assert query["redirect_uri"] == ["https://app.example.in/api/v1/calendar/callback/google/"]
    assert query["access_type"] == ["offline"]
    state = query["state"][0]

    tokens = ({"access_token": "a", "refresh_token": "r", "expires_in": 3600}, "mehta@gmail.com")
    with patch("apps.appointments.calendar_sync.Google.exchange", return_value=tokens), \
            patch("apps.appointments.tasks.sync_doctor_calendar.delay") as delay:
        back = api_client.get(f"/api/v1/calendar/callback/google/?code=c&state={state}")
        again = api_client.get(f"/api/v1/calendar/callback/google/?code=c&state={state}")
    assert back.status_code == 302 and "calendar=connected" in back["Location"]
    assert "calendar=error" in again["Location"]  # one-time state
    conn = CalendarConnection.objects.get(doctor=doctor)
    assert (conn.account_email, conn.refresh_token, conn.status) == ("mehta@gmail.com", "r", "active")
    delay.assert_called_once_with(conn.pk)


@pytest.mark.django_db
@override_settings(**GOOGLE)
def test_tampered_state_is_rejected(api_client):
    r = api_client.get("/api/v1/calendar/callback/google/?code=c&state=forged")
    assert "calendar=error" in r["Location"]
    assert not CalendarConnection.objects.exists()


@pytest.mark.django_db
def test_connect_is_refused_when_the_provider_is_not_configured(auth_client, doctor):
    r = auth_client.post("/api/v1/calendar/connect/", {"doctor": doctor.pk, "provider": "microsoft"}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_a_doctor_without_staff_rights_only_sees_their_own_calendar(restricted_client, restricted_user, hospital, department):
    mine = Doctor.objects.create(hospital=hospital, department=department, name="Me", user=restricted_user)
    Doctor.objects.create(hospital=hospital, department=department, name="Someone else")
    r = restricted_client.get("/api/v1/calendar/connections/")
    assert [d["doctor_id"] for d in r.data["doctors"]] == [mine.pk]


@pytest.mark.django_db
def test_disconnect_lifts_calendar_blocks_and_forgets_the_tokens(auth_client, hospital, doctor, conn):
    s = _slot(hospital, doctor, 1, 10)
    with patch("apps.appointments.calendar_sync.provider_for", return_value=_provider(busy=[("g1", _local(1, 10), _local(1, 11))])):
        sync_busy(conn)
        assert auth_client.delete(f"/api/v1/calendar/connections/{conn.pk}/").status_code == 204
    s.refresh_from_db()
    assert not s.is_blocked
    assert not CalendarConnection.objects.exists()


@pytest.mark.django_db
def test_connections_are_tenant_isolated(auth_client, other_hospital):
    theirs_doctor = Doctor.objects.create(hospital=other_hospital, name="Theirs")
    theirs = CalendarConnection.objects.create(hospital=other_hospital, doctor=theirs_doctor, provider="google")
    assert auth_client.delete(f"/api/v1/calendar/connections/{theirs.pk}/").status_code == 404
    assert CalendarConnection.objects.filter(pk=theirs.pk).exists()
