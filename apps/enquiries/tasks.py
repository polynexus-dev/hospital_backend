from celery import shared_task

from apps.core.models import Hospital

from .scoring import recompute_hospital_scores


@shared_task
def escalate_overdue_enquiries():
    """First-contact SLA breaches and overdue follow-ups: tasks for the owner,
    then a manager (and reassignment, per the hospital's escalation rules).
    See apps.enquiries.sla."""
    from .sla import escalate_all

    return escalate_all()


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


@shared_task
def sweep_quiet_enquiries():
    from .quiet import sweep_quiet_enquiries as run

    return run()
