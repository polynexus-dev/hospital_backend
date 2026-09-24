import logging

from django.db import transaction
from django.dispatch import receiver

from apps.ipd.signals import patient_discharged

logger = logging.getLogger(__name__)


@receiver(patient_discharged)
def post_final_bed_charges(sender, admission, **kwargs):
    """Final bed-days go on the bill at discharge. A billing problem is
    logged, never allowed to block the clinical discharge."""
    from .bed_charges import policy_for, post_bed_charges

    if not policy_for(admission.hospital_id).auto_post:
        return
    try:
        with transaction.atomic():
            post_bed_charges(admission)
    except Exception:
        logger.exception("Final bed-charge posting failed for admission %s", admission.pk)
