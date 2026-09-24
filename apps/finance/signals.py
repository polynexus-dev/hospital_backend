"""Auto-posting to the double-entry ledger and claim-status notifications.
Posting failures are logged, never raised — a ledger hiccup must not stop
a cashier from saving a bill."""
import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _safe(fn, obj):
    try:
        with transaction.atomic():
            fn(obj)
    except Exception:
        logger.exception("Ledger posting failed for %s %s", type(obj).__name__, getattr(obj, "pk", None))


@receiver(post_save, sender="billing.Bill")
def bill_posted(sender, instance, **kwargs):
    from .accounting import post_bill

    _safe(post_bill, instance)


@receiver(post_save, sender="billing.Payment")
def payment_posted(sender, instance, created, **kwargs):
    if created:
        from .accounting import post_payment

        _safe(post_payment, instance)


@receiver(pre_save, sender="tpa.Claim")
def remember_claim_status(sender, instance, **kwargs):
    instance._previous_status = sender.objects.filter(pk=instance.pk).values_list("status", flat=True).first() if instance.pk else None


@receiver(post_save, sender="tpa.Claim")
def claim_status_notify(sender, instance, created, **kwargs):
    """FPM.4.f — tell the patient whenever their claim moves."""
    previous = getattr(instance, "_previous_status", None)
    if created or previous == instance.status:
        return
    from apps.clinical.notify import notify_patient

    notify_patient(instance.patient, "claim_status", {"claim_number": instance.claim_number or f"#{instance.pk}", "status": instance.get_status_display()})
