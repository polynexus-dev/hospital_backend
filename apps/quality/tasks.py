import time

from celery import shared_task
from django.db import connection
from django.utils import timezone

from apps.core.models import Hospital

from . import kpis
from .models import SystemHeartbeat


@shared_task
def record_heartbeat():
    t0 = time.monotonic()
    with connection.cursor() as c:
        c.execute("SELECT 1")
    SystemHeartbeat.objects.create(db_latency_ms=int((time.monotonic() - t0) * 1000))


@shared_task
def publish_previous_quarter_kpis():
    """IMS.2.c — runs daily; publishes last quarter once, in the first week
    of a new quarter."""
    today = timezone.localdate()
    if today.day > 7 or today.month not in (1, 4, 7, 10):
        return 0
    start, end = kpis.previous_quarter(today)
    from .models import KPISnapshot

    n = 0
    for hospital in Hospital.objects.filter(is_active=True):
        if KPISnapshot.objects.filter(hospital=hospital, period_start=start, period_end=end, is_published=True).exists():
            continue
        kpis.snapshot(hospital.pk, start, end, publish=True)
        n += 1
    return n
