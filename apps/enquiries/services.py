from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from apps.accounts.models import User

from .models import Enquiry, EnquiryAssignmentChange, EnquiryStageChange

# Default SLA for a fresh enquiry to be first contacted (§2 ageing report).
DEFAULT_ENQUIRY_SLA_HOURS = 4

OPEN_STAGES = [Enquiry.Stage.NEW, Enquiry.Stage.CONTACTED, Enquiry.Stage.SCHEDULED, Enquiry.Stage.FOLLOW_UP]


def find_duplicates(hospital, *, mobile="", alternate_mobile="", email=""):
    """Matches on mobile, alternate mobile, or email within the hospital —
    the union covers a patient calling from a different number than the one
    on file (§2)."""
    numbers = [n for n in (mobile, alternate_mobile) if n]
    q = Q()
    if numbers:
        q |= Q(mobile__in=numbers) | Q(alternate_mobile__in=numbers)
    if email:
        q |= Q(email=email)
    if not q:
        return Enquiry.objects.none()
    return Enquiry.objects.filter(hospital=hospital).filter(q)


def assign_enquiry(enquiry: Enquiry) -> User | None:
    """Round-robin / department-wise / skill-based assignment, approximated
    as least-currently-loaded active user in the target department (§2).
    Falls back to least-loaded active user hospital-wide if the enquiry has
    no department yet."""
    candidates = User.objects.filter(hospital_id=enquiry.hospital_id, is_active=True)
    if enquiry.department_id:
        department_candidates = candidates.filter(department_id=enquiry.department_id)
        if department_candidates.exists():
            candidates = department_candidates

    candidates = candidates.annotate(
        open_load=Count("assigned_enquiries", filter=Q(assigned_enquiries__stage__in=OPEN_STAGES))
    ).order_by("open_load", "id")

    owner = candidates.first()
    if owner is None:
        return None

    reassign_enquiry(enquiry, owner, reason="auto-assigned")
    return owner


def reassign_enquiry(enquiry: Enquiry, owner: User | None, *, changed_by=None, reason: str = "") -> Enquiry:
    """Sets the lead owner and logs the change (§2) — the single write path
    both auto-assignment and manual reassignment go through, so ownership
    history is always complete regardless of how the owner was picked."""
    previous_owner_id = enquiry.assigned_to_id
    if previous_owner_id == getattr(owner, "id", None):
        return enquiry

    enquiry.assigned_to = owner
    enquiry.save(update_fields=["assigned_to"])

    EnquiryAssignmentChange.objects.create(
        hospital=enquiry.hospital,
        enquiry=enquiry,
        from_owner_id=previous_owner_id,
        to_owner=owner,
        changed_by=changed_by,
        reason=reason,
    )
    return enquiry


def merge_enquiries(primary: Enquiry, duplicate: Enquiry, *, merged_by=None) -> Enquiry:
    """Consolidates a duplicate lead into the primary one (§2): marks the
    duplicate as merged, closes it out of the open pipeline, and folds its
    notes into the primary record so no context is lost."""
    if primary.pk == duplicate.pk:
        raise ValueError("Cannot merge an enquiry into itself.")

    if duplicate.notes:
        marker = f"[Merged from enquiry #{duplicate.pk}] {duplicate.notes}"
        primary.notes = f"{primary.notes}\n{marker}".strip() if primary.notes else marker
        primary.save(update_fields=["notes"])

    duplicate.duplicate_of = primary
    duplicate.lost_reason = Enquiry.LostReason.DUPLICATE
    duplicate.save(update_fields=["duplicate_of", "lost_reason"])
    move_stage(duplicate, Enquiry.Stage.LOST, changed_by=merged_by)
    return primary


def move_stage(enquiry: Enquiry, to_stage: str, *, changed_by=None) -> Enquiry:
    from_stage = enquiry.stage
    if from_stage == to_stage:
        return enquiry

    enquiry.stage = to_stage
    if to_stage in OPEN_STAGES and enquiry.sla_due_at is None:
        enquiry.sla_due_at = timezone.now() + timedelta(hours=DEFAULT_ENQUIRY_SLA_HOURS)
    enquiry.save(update_fields=["stage", "sla_due_at"])

    EnquiryStageChange.objects.create(
        hospital=enquiry.hospital,
        enquiry=enquiry,
        from_stage=from_stage,
        to_stage=to_stage,
        changed_by=changed_by,
    )
    return enquiry
