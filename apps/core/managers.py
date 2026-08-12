from django.db import models

from .tenancy import get_current_hospital_id


class TenantQuerySet(models.QuerySet):
    def for_hospital(self, hospital):
        hospital_id = hospital.id if hasattr(hospital, "id") else hospital
        return self.filter(hospital_id=hospital_id)


class TenantManager(models.Manager):
    """
    Auto-scopes queries to the current hospital (see tenancy.py) when one is
    set on the request context. Falls back to unscoped when there isn't one
    (management commands, migrations, Celery beat) — callers in that
    situation are responsible for filtering explicitly if they need to.
    """

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db)
        hospital_id = get_current_hospital_id()
        if hospital_id is not None:
            qs = qs.filter(hospital_id=hospital_id)
        return qs

    def unscoped(self):
        return TenantQuerySet(self.model, using=self._db)
