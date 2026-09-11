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

    #: Override in a subclass to attach model-specific queryset methods
    #: (e.g. apps.patients.models.PatientQuerySet.by_mobile) while keeping
    #: this manager's hospital-scoping behavior.
    queryset_class = TenantQuerySet

    def get_queryset(self):
        qs = self.queryset_class(self.model, using=self._db)
        hospital_id = get_current_hospital_id()
        if hospital_id is not None:
            qs = qs.filter(hospital_id=hospital_id)
        return qs

    def unscoped(self):
        return self.queryset_class(self.model, using=self._db)


class SoftDeleteQuerySet(TenantQuerySet):
    def delete(self):
        """Bulk `.update()`-backed delete (Part A #1 — never hard-delete
        patient-identifying/clinical records). Mirrors
        apps.core.models.SoftDeleteModel.delete() for the single-instance
        case; QuerySet.delete() bypasses Model.delete() entirely so it
        needs its own override here."""
        from django.utils import timezone
        return super().update(is_deleted=True, deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(TenantManager):
    queryset_class = SoftDeleteQuerySet

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def all_with_deleted(self):
        """Hospital-scoped (when a current hospital is set) but includes
        soft-deleted rows — for admin/audit tooling and the retention purge
        job, not for normal application code."""
        qs = self.queryset_class(self.model, using=self._db)
        hospital_id = get_current_hospital_id()
        if hospital_id is not None:
            qs = qs.filter(hospital_id=hospital_id)
        return qs
