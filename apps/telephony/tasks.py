from celery import shared_task
from django.db.models import F
from django.utils import timezone

from apps.core.models import Hospital

from .models import CallbackTask


@shared_task
def escalate_overdue_callbacks():
    """Runs on a Celery beat schedule. Bumps the escalation level of any
    pending/in-progress callback past its SLA so it surfaces on the
    escalation matrix (§1) instead of quietly ageing out. Looped per
    active hospital rather than one bulk .update() spanning every
    tenant's table — see apps.enquiries.tasks.escalate_overdue_enquiries
    for the same fix applied there."""
    escalated = 0
    for hospital in Hospital.objects.filter(is_active=True):
        overdue = CallbackTask.objects.filter(
            hospital=hospital,
            status__in=[CallbackTask.Status.PENDING, CallbackTask.Status.IN_PROGRESS],
            sla_due_at__lt=timezone.now(),
        )
        escalated += overdue.update(
            status=CallbackTask.Status.ESCALATED,
            escalation_level=F("escalation_level") + 1,
        )
    return escalated
