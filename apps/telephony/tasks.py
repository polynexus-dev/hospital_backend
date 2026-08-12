from celery import shared_task
from django.db.models import F
from django.utils import timezone

from .models import CallbackTask


@shared_task
def escalate_overdue_callbacks():
    """Runs on a Celery beat schedule. Bumps the escalation level of any
    pending/in-progress callback past its SLA so it surfaces on the
    escalation matrix (§1) instead of quietly ageing out."""
    overdue = CallbackTask.objects.filter(
        status__in=[CallbackTask.Status.PENDING, CallbackTask.Status.IN_PROGRESS],
        sla_due_at__lt=timezone.now(),
    )
    return overdue.update(
        status=CallbackTask.Status.ESCALATED,
        escalation_level=F("escalation_level") + 1,
    )
