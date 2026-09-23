from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from .models import Task, Workflow


@shared_task
def sweep_patient_recalls():
    """Preventive-care / follow-up recall sweep (retention) — the
    patient-side counterpart to apps.enquiries.tasks.escalate_overdue_enquiries.
    Finds patients whose next_recall_due_at has passed, creates a follow-up
    Task, and fires the patient_recall_due workflow trigger so a hospital
    can wire an automatic WhatsApp/SMS nudge to it without a code change.
    Clears next_recall_due_at once actioned — the next visit / a workflow
    step is expected to set the next one."""
    from apps.patients.models import Patient

    from .engine import execute_workflow

    due_patients = Patient.objects.filter(next_recall_due_at__lte=timezone.now(), is_active=True)
    patient_content_type = ContentType.objects.get_for_model(Patient)

    created = 0
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
def purge_expired_soft_deleted_records():
    """Completes a right-to-erasure / soft-delete request for real (DPDP
    Act 2023 + Rules 2025 — Part A #12). Once a patient-identifying or
    clinical record has been soft-deleted (apps.core.models.
    SoftDeleteModel) for longer than settings.SOFT_DELETE_PURGE_GRACE_DAYS,
    this removes it from the database outright.

    Deliberately does NOT touch any *active* record — this is not a
    general "old records get deleted" sweep. How long a live clinical
    record must be *retained* is a medical-records-law question (state
    Medical Council rules, ICMR guidance) this codebase has no
    authoritative answer for; auto-purging active records on an age
    threshold would risk violating that retention obligation, not
    fulfilling DPDP's data-minimization one. Only records someone (staff
    action, or a data-principal erasure request) already decided should go
    are eligible here — this job just finishes the job after the grace
    period, rather than leaving soft-deleted rows in the database forever."""
    from apps.patients.models import Document, Patient, Prescription

    cutoff = timezone.now() - timedelta(days=settings.SOFT_DELETE_PURGE_GRACE_DAYS)
    purged = 0
    for model in (Patient, Document, Prescription):
        expired = model.objects.all_with_deleted().filter(is_deleted=True, deleted_at__lt=cutoff)
        purged += expired.count()
        expired.hard_delete()
    return purged


@shared_task
def purge_stale_unconverted_enquiries():
    """DPDP Act 2023 data-minimization (Part A #12) for pre-patient lead
    data: an Enquiry that never became a Patient carries no clinical/
    medical-record retention obligation, so once it's older than
    settings.DEFAULT_DATA_RETENTION_DAYS it's deleted outright rather than
    kept indefinitely "just in case". An Enquiry that did convert
    (patient_id set) is left alone — that history now belongs to the
    patient's record, not just a marketing lead, and follows whatever
    retention the patient's own records are subject to."""
    from apps.enquiries.models import Enquiry

    cutoff = timezone.now() - timedelta(days=settings.DEFAULT_DATA_RETENTION_DAYS)
    stale = Enquiry.objects.filter(patient__isnull=True, created_at__lt=cutoff)
    count = stale.count()
    stale.delete()
    return count


@shared_task
def escalate_overdue_tasks():
    """Bumps priority on generic automation tasks past their due date so
    they surface above routine work (§6 escalation rules)."""
    overdue = Task.objects.filter(
        status__in=[Task.Status.PENDING, Task.Status.IN_PROGRESS],
        due_at__lt=timezone.now(),
    ).exclude(priority=Task.Priority.URGENT)

    updated = 0
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
