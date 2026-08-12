from celery import shared_task
from django.db.models import F
from django.utils import timezone

from .models import Enquiry
from .services import OPEN_STAGES


@shared_task
def escalate_overdue_enquiries():
    """Ageing report / auto-escalation on SLA breach (§2)."""
    overdue = Enquiry.objects.filter(
        stage__in=OPEN_STAGES,
        sla_due_at__lt=timezone.now(),
    )
    return overdue.update(escalation_level=F("escalation_level") + 1)
