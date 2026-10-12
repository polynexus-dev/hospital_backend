"""Security incident / breach register (CERT-In 2022, DPDP Act 2023) and 180-day log retention.
NABH DOM.3.a (critical security incidents), DAC.2.c (log retention).
"""
import logging
from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.logging_handlers import DailyFileHandler, purge_old_logs
from apps.governance.models import AuditRule, SecurityEvent, SecurityIncident
from apps.governance.services import MIN_LOG_RETENTION_DAYS, flag_overdue_incidents

BREACH = {
    "title": "Laptop with discharge summaries stolen", "description": "Ward laptop taken from nursing station.",
    "category": "lost_device", "severity": "high", "personal_data_affected": True, "records_affected": 40,
}


@pytest.mark.django_db
def test_any_staff_member_can_report_but_only_admins_manage(auth_client, restricted_user):
    reporter = APIClient()
    reporter.force_authenticate(user=restricted_user)
    res = reporter.post("/api/v1/governance/incidents/", BREACH, format="json")
    assert res.status_code == 201, res.data
    assert res.data["reported_by"] == restricted_user.pk and res.data["status"] == "open"
    assert SecurityEvent.objects.filter(event_type="incident_reported").exists()
    assert reporter.get("/api/v1/governance/incidents/").status_code == 403
    assert auth_client.get("/api/v1/governance/incidents/").data["count"] == 1
    assert any("Security incident" in m.subject for m in mail.outbox)


@pytest.mark.django_db
def test_deadlines_run_from_detection_and_overdue_steps_alert_once(auth_client):
    detected = timezone.now() - timedelta(hours=7)
    pk = auth_client.post("/api/v1/governance/incidents/", {**BREACH, "detected_at": detected.isoformat()}, format="json").data["id"]
    incident = SecurityIncident.objects.get(pk=pk)
    assert incident.cert_in_due_at == detected + timedelta(hours=6)
    assert incident.dpb_report_due_at == detected + timedelta(hours=72)
    assert incident.overdue() == ["cert_in"]  # 7h in: CERT-In late; DPDP steps not yet

    assert flag_overdue_incidents() == 1
    assert flag_overdue_incidents() == 0  # once per step
    event = SecurityEvent.objects.get(event_type="incident_overdue")
    assert event.severity == "critical" and event.details == {"incident": pk, "step": "cert_in"}

    auth_client.post(f"/api/v1/governance/incidents/{pk}/cert-in-reported/", {"reference": "CERTIn-2026-1234"}, format="json")
    incident.refresh_from_db()
    assert incident.cert_in_reference == "CERTIn-2026-1234" and incident.overdue() == []
    assert set(incident.overdue(timezone.now() + timedelta(days=3))) == {"dpb_intimation", "dpb_report", "patients"}


@pytest.mark.django_db
def test_an_incident_closes_only_when_every_report_and_the_root_cause_are_recorded(auth_client):
    pk = auth_client.post("/api/v1/governance/incidents/", BREACH, format="json").data["id"]
    url = f"/api/v1/governance/incidents/{pk}/"
    auth_client.post(url + "contain/", {"containment_actions": "Remote-wiped the laptop; reset ward passwords."}, format="json")
    res = auth_client.post(url + "close/")
    assert res.status_code == 400 and "cert_in" in res.data["detail"]
    for step in ("cert-in-reported", "dpb-intimated", "dpb-report-submitted", "patients-notified"):
        assert auth_client.post(url + step + "/").status_code == 200
    assert "root cause" in auth_client.post(url + "close/").data["detail"]
    auth_client.patch(url, {"root_cause": "Laptop left unlocked in a public area."}, format="json")
    closed = auth_client.post(url + "close/")
    assert closed.status_code == 200 and closed.data["status"] == "closed" and closed.data["closed_at"]
    assert auth_client.delete(url).status_code == 405


@pytest.mark.django_db
def test_audit_rules_cannot_keep_logs_for_less_than_180_days(hospital):
    rule = AuditRule(hospital=hospital, name="Short", retention_days=30)
    with pytest.raises(ValidationError):
        rule.full_clean()
    assert MIN_LOG_RETENTION_DAYS == 180


def test_daily_log_files_and_180_day_purge(tmp_path):
    handler = DailyFileHandler(str(tmp_path), name="web")
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test.daily")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("patient record opened")
    finally:
        logger.removeHandler(handler)
        handler.close()
    today = timezone.now().date().isoformat()
    assert "INFO patient record opened" in (tmp_path / f"web-{today}.log").read_text()

    old = tmp_path / f"web-{(date.today() - timedelta(days=181)).isoformat()}.log"
    recent = tmp_path / f"web-{(date.today() - timedelta(days=179)).isoformat()}.log"
    old.write_text("x")
    recent.write_text("x")
    assert purge_old_logs(str(tmp_path), keep_days=30) == 1  # asking for 30 still keeps 180
    assert not old.exists() and recent.exists()
