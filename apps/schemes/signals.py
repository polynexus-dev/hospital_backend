import logging

from django.db import transaction
from django.dispatch import receiver

from apps.ipd.signals import patient_discharged

logger = logging.getLogger(__name__)


@receiver(patient_discharged)
def scheme_case_on_discharge(sender, admission, **kwargs):
    """At discharge: final bed-days, scheme re-pricing, and the claim clock starts.
    Never blocks the clinical discharge."""
    from apps.billing.bed_charges import policy_for, post_bed_charges

    from . import services
    from .models import SchemeCase

    for case in SchemeCase.objects.filter(admission=admission, status__in=["draft", "preauth_approved"]):
        try:
            with transaction.atomic():
                if policy_for(admission.hospital_id).auto_post:
                    post_bed_charges(admission)
                services.apply_to_bill(case)
                if case.status == "preauth_approved" or not case.scheme.preauth_required:
                    services.transition(case, "discharged", None, note="Patient discharged")
        except Exception:
            logger.exception("Scheme processing at discharge failed for case %s", case.pk)
