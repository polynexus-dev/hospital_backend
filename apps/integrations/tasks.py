from celery import shared_task

from apps.core.models import Hospital

from .services import sync_his_data


@shared_task
def sync_all_hospitals_his_data():
    """§15 — bi-directional HIS sync for patients and appointments, run
    periodically on Celery beat. A no-op per hospital until a concrete
    HISConnector is configured (settings.HIS_CONNECTOR)."""
    results = {}
    for hospital in Hospital.objects.filter(is_active=True):
        results[str(hospital.id)] = sync_his_data(hospital)
    return results
