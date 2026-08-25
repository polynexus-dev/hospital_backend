from celery import shared_task
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.core.models import Hospital

from .models import Task, Workflow


@shared_task
def sweep_patient_recalls():
    """Preventive-care / follow-up recall sweep (retention) — the
    patient-side counterpart to apps.enquiries.tasks.escalate_overdue_enquiries.
    Finds patients whose next_recall_due_at has passed, creates a follow-up
    Task, and fires the patient_recall_due workflow trigger so a hospital
    can wire an automatic WhatsApp/SMS nudge to it without a code change.
    Clears next_recall_due_at once actioned — the next visit / a workflow
    step is expected to set the next one.

    Looped per active hospital (rather than one query spanning every
    tenant) so a suspended hospital's patients stop being swept —
    apps.core.permissions.HospitalActive locks its users out of the API,
    and this background job shouldn't keep generating recall tasks/
    WhatsApp nudges for it either."""
    from apps.patients.models import Patient

    from .engine import execute_workflow

    patient_content_type = ContentType.objects.get_for_model(Patient)

    created = 0
    for hospital in Hospital.objects.filter(is_active=True):
        due_patients = Patient.objects.filter(hospital=hospital, next_recall_due_at__lte=timezone.now(), is_active=True)
        for patient in due_patients.iterator():
            Task.objects.create(
                hospital=patient.hospital,
                title=f"Recall due: {patient.full_name}",
                description=patient.recall_reason or "Preventive-care / follow-up recall due.",
                priority=Task.Priority.NORMAL,
                content_type=patient_content_type,
                object_id=patient.pk,
            )
            execute_workflow(
                Workflow.TriggerType.PATIENT_RECALL_DUE,
                {"patient_id": patient.id, "patient_name": patient.full_name, "recall_reason": patient.recall_reason},
                patient.hospital_id,
            )
            patient.next_recall_due_at = None
            patient.save(update_fields=["next_recall_due_at"])
            created += 1
    return created


@shared_task
def escalate_overdue_tasks():
    """Bumps priority on generic automation tasks past their due date so
    they surface above routine work (§6 escalation rules). Looped per
    active hospital — see sweep_patient_recalls above for why."""
    updated = 0
    for hospital in Hospital.objects.filter(is_active=True):
        overdue = Task.objects.filter(
            hospital=hospital,
            status__in=[Task.Status.PENDING, Task.Status.IN_PROGRESS],
            due_at__lt=timezone.now(),
        ).exclude(priority=Task.Priority.URGENT)

        for task in overdue:
            next_priority = {
                Task.Priority.LOW: Task.Priority.NORMAL,
                Task.Priority.NORMAL: Task.Priority.HIGH,
                Task.Priority.HIGH: Task.Priority.URGENT,
            }.get(task.priority, Task.Priority.URGENT)
            task.priority = next_priority
            task.save(update_fields=["priority"])
            updated += 1
    return updated
