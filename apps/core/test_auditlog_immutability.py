"""AuditLog stays immutable, yet users/hospitals can still be deleted and the
DAC.2.c retention job can still purge (core migrations 0016/0017). The raw-SQL
checks only mean something on PostgreSQL, where the trigger exists."""
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog

postgres_only = pytest.mark.skipif(connection.vendor != "postgresql", reason="immutability trigger is PostgreSQL-only")


def test_deleting_a_user_leaves_their_audit_rows_untouched(hospital):
    actor = User.objects.create_user(email="leaver@test-hospital.example", password="x-Pass-1234", hospital=hospital)
    log = AuditLog.objects.create(hospital=hospital, actor=actor, action=AuditLog.Action.UPDATE, model_name="patient", object_id="1")

    actor_id = actor.pk
    actor.delete()  # used to fail on PostgreSQL: SET_NULL is an UPDATE the trigger refuses

    log = AuditLog.objects.get(pk=log.pk)
    assert log.actor_id == actor_id  # who did it is kept, not nulled


def test_api_shows_a_deleted_actor_instead_of_crashing(auth_client, user, hospital):
    leaver = User.objects.create_user(email="gone@test-hospital.example", password="x-Pass-1234", hospital=hospital)
    AuditLog.objects.create(hospital=hospital, actor=leaver, action=AuditLog.Action.UPDATE, model_name="patient", object_id="2")
    leaver_id = leaver.pk
    leaver.delete()
    user.is_staff = True  # the audit trail is Console-only (IsAdminUser)
    user.save(update_fields=["is_staff"])

    res = auth_client.get("/api/v1/audit-logs/", {"actor": leaver_id})
    assert res.status_code == 200
    assert res.json()["results"][0]["actor_email"] == f"deleted user #{leaver_id}"


@postgres_only
def test_raw_update_and_delete_are_still_refused(hospital):
    log = AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.READ, model_name="patient", object_id="3")
    for sql in ("UPDATE core_auditlog SET object_id = 'x' WHERE id = %s", "DELETE FROM core_auditlog WHERE id = %s"):
        with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, [log.pk])
    assert AuditLog.objects.filter(pk=log.pk, object_id="3").exists()


@postgres_only
def test_retention_purge_can_delete_expired_rows(hospital, tmp_path, settings):
    from apps.governance.models import AuditRule
    from apps.governance.services import MIN_LOG_RETENTION_DAYS, purge_expired_audit_logs

    settings.MEDIA_ROOT = str(tmp_path)  # the archive is written under MEDIA_ROOT/backups
    AuditRule.objects.create(hospital=hospital, name="All", retention_days=MIN_LOG_RETENTION_DAYS)
    rows = [AuditLog.objects.create(hospital=hospital, action=AuditLog.Action.READ, model_name="patient", object_id=str(i)) for i in range(2)]

    # run "a retention period from now", so both rows are past it
    purged = purge_expired_audit_logs(now=timezone.now() + timedelta(days=MIN_LOG_RETENTION_DAYS + 1))

    assert purged == 2
    assert not AuditLog.objects.filter(pk__in=[r.pk for r in rows]).exists()
    assert list((tmp_path / "backups").glob("auditlog-archive-*.json.gz"))  # archived before deletion
