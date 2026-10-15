"""Inbox realism. The inbox shows a thread's patient name and a message
preview only when they appear in the first page of /patients/ and of the
recent-messages feed (page size 25). Seeded messages were all stamped with
the seed time, and the first patient page is not in id order, so: date each
message at its sent time, and hand the WhatsApp threads (with their
messages) to patients on the first page the application actually serves."""
from django.db.models import F
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.communications.models import Message, Thread
from apps.telephony.models import CallbackTask


def run(ctx):
    h = ctx["h"]
    owner = User.objects.filter(hospital=h, email="owner@demo-hospital.example").first()
    client = APIClient()
    client.force_authenticate(owner)
    first_page = [r["id"] for r in client.get("/api/v1/patients/").json()["results"]]
    page_users = [r["id"] for r in client.get("/api/v1/users/").json()["results"]]
    threads = list(Thread.objects.filter(hospital=h, channel="whatsapp").order_by("-last_message_at"))
    have = {th.patient_id for th in threads}
    targets = [pid for pid in first_page if pid not in have]
    movers = [th for th in threads if th.patient_id not in first_page]
    moved = 0
    for th, pid in zip(movers, targets):
        Message.objects.filter(hospital=h, patient_id=th.patient_id, channel="whatsapp").update(patient_id=pid)
        Thread.objects.filter(pk=th.pk).update(patient_id=pid)
        moved += 1
    Message.objects.filter(hospital=h, sent_at__isnull=False).update(created_at=F("sent_at"))
    agents = list(User.objects.filter(pk__in=page_users, role__name__in=["CRM Manager", "Call Centre Executive", "Patient Relations Executive", "Front Office"]))
    if agents:
        for i, th in enumerate(Thread.objects.filter(hospital=h, owner__isnull=False).exclude(owner_id__in=page_users)):
            Thread.objects.filter(pk=th.pk).update(owner=agents[i % len(agents)])
        for i, t in enumerate(CallbackTask.objects.filter(hospital=h, owner__isnull=False).exclude(owner_id__in=page_users)):
            CallbackTask.objects.filter(pk=t.pk).update(owner=agents[i % len(agents)])
    return f"{moved} threads re-homed to first-page patients, messages dated at send time"
