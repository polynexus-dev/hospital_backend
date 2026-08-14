from django.db import transaction
from django.utils import timezone

from apps.facilities.models import Bed

from .models import Admission, BedAllocation, DischargeSummary, WardTransfer


class BedUnavailable(Exception):
    pass


class DischargeSummaryRequired(Exception):
    pass


@transaction.atomic
def admit_patient(*, hospital, patient, admitting_doctor, bed, admission_type=Admission.AdmissionType.PLANNED,
                   department=None, admission_diagnosis="", source_encounter=None) -> Admission:
    """select_for_update mirrors apps.appointments.services.book_appointment's
    slot-locking — two simultaneous admission requests for the same bed
    can't both succeed."""
    locked_bed = Bed.objects.select_for_update().get(pk=bed.pk)
    if locked_bed.status != Bed.Status.AVAILABLE:
        raise BedUnavailable(f"Bed {locked_bed} is not available (status: {locked_bed.get_status_display()}).")

    admission = Admission.objects.create(
        hospital=hospital, patient=patient, admitting_doctor=admitting_doctor, department=department,
        bed=locked_bed, admission_type=admission_type, admission_diagnosis=admission_diagnosis,
        source_encounter=source_encounter,
    )
    BedAllocation.objects.create(hospital=hospital, admission=admission, bed=locked_bed)
    locked_bed.status = Bed.Status.OCCUPIED
    locked_bed.current_admission = admission
    locked_bed.save(update_fields=["status", "current_admission"])
    return admission


@transaction.atomic
def request_ward_transfer(*, admission: Admission, to_bed: Bed, reason: str, requested_by) -> WardTransfer:
    return WardTransfer.objects.create(
        hospital=admission.hospital, admission=admission, from_bed=admission.bed, to_bed=to_bed,
        reason=reason, requested_by=requested_by,
    )


@transaction.atomic
def approve_ward_transfer(transfer: WardTransfer, *, approved_by) -> WardTransfer:
    """Locks both the destination bed and the admission's current bed for
    the duration of the transaction — same race-safety reasoning as
    admit_patient above."""
    locked_to_bed = Bed.objects.select_for_update().get(pk=transfer.to_bed_id)
    if locked_to_bed.status != Bed.Status.AVAILABLE:
        raise BedUnavailable(f"Bed {locked_to_bed} is not available (status: {locked_to_bed.get_status_display()}).")

    admission = transfer.admission
    old_bed = Bed.objects.select_for_update().get(pk=admission.bed_id)

    BedAllocation.objects.filter(admission=admission, released_at__isnull=True).update(released_at=timezone.now())
    old_bed.status = Bed.Status.AVAILABLE
    old_bed.current_admission = None
    old_bed.save(update_fields=["status", "current_admission"])

    BedAllocation.objects.create(hospital=admission.hospital, admission=admission, bed=locked_to_bed)
    locked_to_bed.status = Bed.Status.OCCUPIED
    locked_to_bed.current_admission = admission
    locked_to_bed.save(update_fields=["status", "current_admission"])

    admission.bed = locked_to_bed
    admission.save(update_fields=["bed"])

    transfer.approved_by = approved_by
    transfer.transferred_at = timezone.now()
    transfer.save(update_fields=["approved_by", "transferred_at"])
    return transfer


@transaction.atomic
def discharge_patient(admission: Admission, *, status: str = Admission.Status.DISCHARGED) -> Admission:
    """Requires a DischargeSummary to already exist (not necessarily
    finalized — a hospital may finalize the paperwork shortly after the
    patient physically leaves) — see docs/erp/07-audit-and-security.md
    §2b and the Phase 4 exit criterion in docs/erp/08-implementation-backlog.md."""
    from .signals import patient_discharged

    if not DischargeSummary.objects.filter(admission=admission).exists():
        raise DischargeSummaryRequired("A discharge summary must be created before discharging this admission.")

    admission.status = status
    admission.discharged_at = timezone.now()
    admission.save(update_fields=["status", "discharged_at"])

    BedAllocation.objects.filter(admission=admission, released_at__isnull=True).update(released_at=timezone.now())
    bed = Bed.objects.select_for_update().get(pk=admission.bed_id)
    bed.status = Bed.Status.AVAILABLE
    bed.current_admission = None
    bed.save(update_fields=["status", "current_admission"])

    patient_discharged.send(sender=Admission, admission=admission)
    return admission
