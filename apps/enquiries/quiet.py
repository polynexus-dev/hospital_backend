"""Leads that went quiet: an open enquiry nobody has heard from for
QUIET_AFTER_DAYS starts the hospital's "Lead went quiet" workflows (a
ready-made follow-up sequence is in apps.automation.workflow_templates).

"Heard from" means the lead did something or we really spoke to them: the
enquiry was created or moved stage, the lead wrote to us, or a call was
answered. Our own automatic follow-ups don't count, so a lead doesn't stop
being quiet just because we messaged them. Each quiet spell starts the
workflows once; when the lead engages again, the next spell can start them
again.
"""
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from .models import Enquiry
from .services import OPEN_STAGES

QUIET_AFTER_DAYS = 2


def with_last_engagement(qs):
    return qs.annotate(
        _last_stage=Max("stage_changes__created_at"),
        _last_inbound=Max("messages__created_at", filter=_inbound()),
        _last_answered=Max("calls__started_at", filter=_answered()),
    )


def _inbound():
    from django.db.models import Q

    return Q(messages__direction="inbound")


def _answered():
    from django.db.models import Q

    return Q(calls__status="answered")


def last_engagement(enquiry):
    """For one enquiry, annotated or not."""
    if not hasattr(enquiry, "_last_stage"):
        enquiry = with_last_engagement(Enquiry.objects.filter(pk=enquiry.pk)).first()
    return max(t for t in (enquiry.created_at, enquiry._last_stage, enquiry._last_inbound, enquiry._last_answered) if t)


def quiet_days(enquiry):
    return round((timezone.now() - last_engagement(enquiry)).total_seconds() / 86400, 1)


def sweep_quiet_enquiries(now=None):
    """Fires enquiry_went_quiet for each open enquiry that has just gone
    quiet, in hospitals with an active workflow for it. Returns how many."""
    from apps.automation.engine import fire
    from apps.automation.models import Workflow

    now = now or timezone.now()
    trigger = Workflow.TriggerType.ENQUIRY_WENT_QUIET
    hospitals = Workflow.objects.filter(trigger_type=trigger, is_active=True).values_list("hospital_id", flat=True).distinct()
    cutoff = now - timedelta(days=QUIET_AFTER_DAYS)
    candidates = with_last_engagement(Enquiry.objects.filter(hospital_id__in=list(hospitals), stage__in=OPEN_STAGES, duplicate_of__isnull=True,
                                                             created_at__lt=cutoff))
    fired = 0
    for enquiry in candidates.iterator():
        last = last_engagement(enquiry)
        if last >= cutoff or (enquiry.went_quiet_at and enquiry.went_quiet_at >= last):
            continue
        Enquiry.objects.filter(pk=enquiry.pk).update(went_quiet_at=now)
        fire(trigger, {"enquiry_id": enquiry.pk, "quiet_days": round((now - last).total_seconds() / 86400, 1)}, enquiry.hospital_id)
        fired += 1
    return fired
