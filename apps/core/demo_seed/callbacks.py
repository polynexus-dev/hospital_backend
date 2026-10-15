"""Callback tasks are raised by the application itself for every missed
inbound call (signal on Call). During seeding that happens at seed time, so
every task looks "missed at <seed time>" and pending. Re-date each task to its
call, keep today's tasks only and give them a realistic mix of outcomes."""
from datetime import timedelta

from django.db.models import Q

from apps.accounts.models import User
from django.utils import timezone

from apps.core.models import Department
from apps.telephony.models import Call, CallbackTask

from .common import RNG, TODAY, at_today, first_page_users, pick

NOTES = ["Called back – appointment booked.", "Called back – report status shared.", "Patient asked to call after 6 pm.",
         "Called back – estimate shared on WhatsApp.", "Wrong number.", "Called back – billing query resolved."]


def run(ctx):
    h = ctx["h"]
    ops = first_page_users(h) or list(User.objects.filter(hospital=h, is_active=True).filter(
        Q(role__name__in=["Call Centre Executive", "Telephony Operator", "CRM Manager"])).distinct())
    # this morning's missed calls: the application raises a callback task for each
    pts = [p for p in ctx["pts"] if p.mobile]
    depts = list(Department.objects.filter(hospital=h))
    now = timezone.now()
    for i in range(12 if not Call.objects.filter(hospital=h, provider_call_id="DEMO-CB-0").exists() else 0):
        p = pts[(i * 17 + 5) % len(pts)]
        since_8 = max(15, int((now - at_today(8, 0)).total_seconds() // 60))
        start = now - timedelta(minutes=min(4 + i * RNG.randint(3, 5), since_8 - i))  # the last hour or so, after 8 am
        Call.objects.create(hospital=h, direction="inbound", status=pick(["missed", "missed", "rnr"]), from_number=p.mobile, to_number="+912067000000",
                            patient=p, department=pick(depts), operator=pick(ops) if ops else None, started_at=start, ended_at=start + timedelta(seconds=25), duration_seconds=0,
                            ivr_path=pick(["1>1", "1>2", "2>1", "3"]), call_reason=pick(["opd", "opd", "report", "billing", "surgical"]),
                            provider_name="demo", provider_call_id=f"DEMO-CB-{i}")
    # The callback queue lists one page ordered by SLA and hides finished tasks, so a long
    # history of done tasks would push today's open ones off the page: keep today's only.
    CallbackTask.objects.filter(hospital=h, call__started_at__date__lt=TODAY).delete()
    done = open_ = 0
    for t in CallbackTask.objects.filter(hospital=h, call__isnull=False).select_related("call"):
        start = t.call.started_at
        fields = {"created_at": start, "sla_due_at": start + timedelta(minutes=30)}
        if now - start > timedelta(minutes=40):  # earlier this morning: mostly called back already
            status = RNG.choices(["done", "escalated"], weights=[5, 1])[0]
        else:
            status = RNG.choices(["pending", "in_progress"], weights=[3, 2])[0]
        if status == "done":
            fields.update(status="done", owner=t.owner if t.owner in ops else (pick(ops) if ops else None), attempt_count=RNG.randint(1, 2),
                          resolved_at=min(start + timedelta(minutes=RNG.randint(5, 25)), now), notes=pick(NOTES))
            done += 1
        else:
            fields.update(status=status, owner=(pick(ops) if ops and status != "pending" else None),
                          escalation_level=1 if status == "escalated" else 0, attempt_count=0 if status == "pending" else 1)
            open_ += 1
        CallbackTask.objects.filter(pk=t.pk).update(**fields)
    return f"today: {done} callbacks done, {open_} open"
