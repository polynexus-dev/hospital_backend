from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.core.models import Hospital

from . import services
from .models import BackupRecord, RetentionPolicy


@shared_task
def run_scheduled_backups():
    """Hourly sweep: backs up each hospital whose last successful scheduled
    backup is older than its configured frequency."""
    now = timezone.now()
    done = 0
    for hospital in Hospital.objects.filter(is_active=True):
        policy = RetentionPolicy.for_hospital(hospital)
        if not policy.auto_backup_enabled:
            continue
        last = BackupRecord.objects.filter(hospital=hospital, status=BackupRecord.Status.SUCCESS).order_by("-started_at").first()
        if last and last.started_at > now - timedelta(hours=policy.auto_backup_frequency_hours):
            continue
        services.run_backup(hospital, kind=BackupRecord.Kind.SCHEDULED)
        done += 1
    return done


@shared_task
def enforce_retention():
    return {
        "backups_purged": services.purge_expired_backups(),
        "audit_logs_archived": services.purge_expired_audit_logs(),
    }
