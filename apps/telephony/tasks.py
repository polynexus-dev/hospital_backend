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


@shared_task
def analyse_call(call_id):
    """Transcribe + summarise one recorded call (apps.telephony.call_ai)."""
    from .call_ai import analyse_call as run
    from .models import Call

    call = Call.objects.filter(pk=call_id).first()
    if call is not None:
        run(call)


@shared_task
def flag_missed_opportunities():
    from .opportunities import flag_missed_opportunities as run

    return run()


@shared_task
def close_stale_ivr_sessions():
    """A PBX that never sent the hang-up event mustn't leave a ghost caller
    at the front of the queue."""
    from datetime import timedelta

    from .ivr import STALE_SESSION_HOURS
    from .models import IVRSession

    cutoff = timezone.now() - timedelta(hours=STALE_SESSION_HOURS)
    stale = IVRSession.objects.filter(updated_at__lt=cutoff)
    closed = stale.filter(state=IVRSession.State.QUEUED).update(state=IVRSession.State.ABANDONED, ended_at=timezone.now())
    closed += stale.filter(state=IVRSession.State.IN_MENU).update(state=IVRSession.State.ENDED, ended_at=timezone.now())
    return closed
