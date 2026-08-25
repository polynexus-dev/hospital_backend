from celery import shared_task
from django.db.models import F
from django.utils import timezone

from apps.core.models import Hospital

from .models import Enquiry
from .services import OPEN_STAGES


@shared_task
def escalate_overdue_enquiries():
    """Ageing report / auto-escalation on SLA breach (§2). Looped per
    active hospital rather than one bulk .update() spanning every
    tenant's table, so a suspended hospital's enquiries stop being
    escalated too — see apps.appointments.tasks.sweep_patient_recalls for
    the same reasoning applied there."""
    escalated = 0
    for hospital in Hospital.objects.filter(is_active=True):
        overdue = Enquiry.objects.filter(
            hospital=hospital,
            stage__in=OPEN_STAGES,
            sla_due_at__lt=timezone.now(),
        )
        escalated += overdue.update(escalation_level=F("escalation_level") + 1)
    return escalated
