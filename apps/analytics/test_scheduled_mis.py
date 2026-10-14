"""Weekly and monthly MIS sent automatically, once per period."""
from datetime import date
from unittest import mock

from django.core import mail

from apps.analytics.models import MISReportDelivery, MISSchedule
from apps.analytics.scheduled_mis import previous_month, previous_week, send_due


def test_periods():
    assert previous_week(date(2026, 10, 14)) == (date(2026, 10, 5), date(2026, 10, 11))  # Wed -> last Mon..Sun
    assert previous_month(date(2026, 11, 1)) == (date(2026, 10, 1), date(2026, 10, 31))
    assert previous_month(date(2026, 3, 1)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_settings_validate_recipients(auth_client):
    bad = auth_client.put("/api/v1/reports/mis-schedule/", {"weekly_enabled": True, "email_recipients": ["not-an-email"]}, format="json")
    assert bad.status_code == 400
    ok = auth_client.put("/api/v1/reports/mis-schedule/", {"weekly_enabled": True, "weekly_day": 0, "monthly_enabled": True,
                                                          "email_recipients": ["owner@hospital.example"], "whatsapp_recipients": ["+91 98220 12345"]}, format="json")
    assert ok.status_code == 200 and ok.json()["whatsapp_recipients"] == ["9822012345"]


def test_weekly_and_monthly_go_out_once_with_the_pdf(hospital):
    MISSchedule.objects.create(hospital=hospital, weekly_enabled=True, weekly_day=0, monthly_enabled=True,
                               email_recipients=["owner@hospital.example"], whatsapp_recipients=["9822012345"])
    with mock.patch("apps.communications.adapters.StubWhatsAppProvider.send", return_value="wa-1") as wa:
        assert send_due(date(2026, 11, 2)) == 1  # a Monday, not the 1st: weekly only
        assert send_due(date(2026, 11, 2)) == 0  # same period isn't sent twice
        assert send_due(date(2026, 12, 1)) == 1  # the 1st (a Tuesday): monthly only
    weekly, monthly = mail.outbox
    assert weekly.subject.startswith("Weekly MIS: Test Hospital, 26 Oct to 01 Nov 2026")
    assert weekly.attachments[0][0].endswith(".pdf") and weekly.attachments[0][1][:4] == b"%PDF"
    assert monthly.subject.startswith("Monthly MIS: Test Hospital, 01 Nov to 30 Nov 2026")
    assert wa.call_count == 2 and "Calls:" in wa.call_args.kwargs["body"]
    assert MISReportDelivery.objects.filter(sent_at__isnull=False).count() == 2


def test_failed_send_is_kept_and_retried_next_day(hospital):
    MISSchedule.objects.create(hospital=hospital, weekly_enabled=True, weekly_day=0, whatsapp_recipients=["9822012345"])
    with mock.patch("apps.communications.adapters.StubWhatsAppProvider.send", side_effect=RuntimeError("provider down")):
        send_due(date(2026, 11, 2))
    d = MISReportDelivery.objects.get()
    assert d.sent_at is None and "provider down" in d.error
    MISReportDelivery.objects.filter(pk=d.pk).update(created_at=d.created_at.replace(year=2026, month=11, day=2))
    with mock.patch("apps.communications.adapters.StubWhatsAppProvider.send", return_value="ok"):
        assert send_due(date(2026, 11, 3)) == 1


def test_send_now_checks_recipients_without_using_up_the_week(auth_client, hospital):
    auth_client.put("/api/v1/reports/mis-schedule/", {"weekly_enabled": True, "email_recipients": ["owner@hospital.example"]}, format="json")
    res = auth_client.post("/api/v1/reports/mis-schedule/")
    assert res.status_code == 200 and res.json()["sent_to"] == ["owner@hospital.example"] and len(mail.outbox) == 1
    assert not MISReportDelivery.objects.exists()


def test_only_managers_set_the_schedule(restricted_client):
    assert restricted_client.get("/api/v1/reports/mis-schedule/").status_code == 403
