from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.patients.models import record_timeline_event

from .models import Enquiry
from .scoring import score_enquiry
from .services import assign_enquiry, find_duplicates


@receiver(post_save, sender=Enquiry)
def on_enquiry_created(sender, instance: Enquiry, created, **kwargs):
    if not created:
        return

    # Day-one heuristic score (see apps.enquiries.scoring's module
    # docstring) — a brand-new enquiry has no stage-change history yet, so
    # there's nothing to gain from trying to train/apply a model for just
    # this one row; the nightly recompute_enquiry_scores task upgrades it
    # to a trained score once the hospital has enough closed-enquiry
    # history, and keeps it current as this enquiry ages.
    instance.score = score_enquiry(instance)
    fields = ["score"]
    if instance.stage == Enquiry.Stage.NEW and instance.sla_due_at is None:
        from .sla import first_contact_due

        instance.sla_due_at = first_contact_due(instance)
        fields.append("sla_due_at")
    instance.save(update_fields=fields)

    if instance.duplicate_of_id is None:
        duplicate = find_duplicates(
            instance.hospital, mobile=instance.mobile, alternate_mobile=instance.alternate_mobile, email=instance.email
        ).exclude(pk=instance.pk).order_by("created_at").first()
        if duplicate is not None:
            instance.duplicate_of = duplicate
            instance.save(update_fields=["duplicate_of"])

    if instance.assigned_to_id is None:
        assign_enquiry(instance)

    from apps.automation.engine import fire

    fire("enquiry_created", {"enquiry_id": instance.pk, "source": instance.source}, instance.hospital_id)

    if instance.patient_id is not None:
        record_timeline_event(
            patient=instance.patient,
            event_type="enquiry",
            summary=f"Enquiry from {instance.get_source_display()}: {instance.service_requested or 'general'}",
            occurred_at=instance.created_at or timezone.now(),
            source=instance,
        )
