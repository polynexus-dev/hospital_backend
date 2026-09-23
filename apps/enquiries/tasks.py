from celery import shared_task
from django.db.models import F
from django.utils import timezone

from apps.core.models import Hospital

from .models import Enquiry
from .scoring import recompute_hospital_scores
from .services import OPEN_STAGES


@shared_task
def escalate_overdue_enquiries():
    """Ageing report / auto-escalation on SLA breach (§2)."""
    overdue = Enquiry.objects.filter(
        stage__in=OPEN_STAGES,
        sla_due_at__lt=timezone.now(),
    )
    return overdue.update(escalation_level=F("escalation_level") + 1)


@shared_task
def recompute_enquiry_scores():
    """Nightly lead-scoring pass, one hospital at a time (see
    apps.enquiries.scoring's module docstring for why training never
    pools across hospitals) — retrains each hospital's model fresh from
    its own closed enquiries and rescoes every still-open one. Returns
    the total number of scores actually changed."""
    updated = 0
    for hospital in Hospital.objects.filter(is_active=True):
        updated += recompute_hospital_scores(hospital)
    return updated
