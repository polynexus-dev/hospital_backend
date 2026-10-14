import logging
from datetime import timedelta

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.patients.models import Patient, record_timeline_event

from .models import Call, CallbackTask, IVRSession

logger = logging.getLogger(__name__)

# Missed / RNR calls must land in the callback queue immediately (§1).
CALLBACK_SLA_MINUTES = 15
OPEN_CALLBACK_STATUSES = [CallbackTask.Status.PENDING, CallbackTask.Status.IN_PROGRESS, CallbackTask.Status.ESCALATED]


@receiver(post_save, sender=Call)
def resolve_patient_and_log_timeline(sender, instance: Call, created, **kwargs):
    if instance.provider_call_id:
        _link_ivr_session(instance)
    queue_analysis(instance)
    if not created:
        return

    if instance.patient_id is None:
        match = Patient.objects.filter(hospital_id=instance.hospital_id).by_mobile(instance.from_number).first()
        if match is not None:
            instance.patient = match
            instance.save(update_fields=["patient"])

    if instance.patient_id is not None:
        record_timeline_event(
            patient=instance.patient,
            event_type="call",
            summary=f"{instance.get_direction_display()} call ({instance.get_status_display()}), {instance.duration_seconds}s",
            occurred_at=instance.started_at,
            source=instance,
        )

    if instance.status in (Call.Status.MISSED, Call.Status.RNR):
        # One open callback per number: a repeat caller, or one who already
        # asked for a callback from the queue, keeps the task they have.
        existing = CallbackTask.objects.filter(hospital_id=instance.hospital_id, phone_number=instance.from_number,
                                               status__in=OPEN_CALLBACK_STATUSES).first()
        if existing is not None:
            if existing.call_id is None:
                CallbackTask.objects.filter(pk=existing.pk).update(call=instance)
            return
        CallbackTask.objects.create(
            hospital=instance.hospital,
            call=instance,
            patient=instance.patient,
            phone_number=instance.from_number,
            department=instance.department,
            source=CallbackTask.Source.MISSED_CALL,
            sla_due_at=timezone.now() + timedelta(minutes=CALLBACK_SLA_MINUTES),
        )


def _link_ivr_session(call):
    """The PBX's finished-call record meets the caller's phone-menu journey:
    copy the path and department onto the call, and close the session."""
    session = IVRSession.objects.filter(hospital_id=call.hospital_id, provider_call_id=call.provider_call_id, call__isnull=True).first()
    if session is None:
        return
    updates = {}
    if not call.ivr_path and session.path:
        updates["ivr_path"] = call.ivr_path = session.path
    if call.department_id is None and session.department_id:
        updates["department_id"] = call.department_id = session.department_id
    if updates:
        Call.objects.filter(pk=call.pk).update(**updates)
    session.call = call
    if session.state in (IVRSession.State.IN_MENU, IVRSession.State.QUEUED):
        if call.status == Call.Status.ANSWERED:
            session.state, session.answered_at = IVRSession.State.ANSWERED, session.answered_at or call.answered_at or timezone.now()
        else:
            session.state = IVRSession.State.ABANDONED if session.state == IVRSession.State.QUEUED else IVRSession.State.ENDED
        session.ended_at = session.ended_at or call.ended_at or timezone.now()
    session.save()


def queue_analysis(call, *, force=False):
    """Queues the AI analysis of a recorded call once (or again, with force:
    the "Analyse again" button). Returns whether it was queued."""
    from .call_ai import should_analyse

    if force:
        if call.ai_status == Call.AIStatus.PENDING:
            return False
        Call.objects.filter(pk=call.pk).update(ai_status=Call.AIStatus.PENDING, ai_error="")
    elif not should_analyse(call) or not Call.objects.filter(pk=call.pk, ai_status="").update(ai_status=Call.AIStatus.PENDING):
        return False  # nothing to analyse, or another save already queued it
    call.ai_status, call.ai_error = Call.AIStatus.PENDING, ""

    def dispatch():
        from .tasks import analyse_call

        try:
            analyse_call.delay(call.pk)
        except Exception:
            logger.exception("Couldn't queue analysis of call %s", call.pk)
            Call.objects.filter(pk=call.pk).update(ai_status=Call.AIStatus.FAILED, ai_error="Couldn't reach the task queue.")

    transaction.on_commit(dispatch)
    return True
