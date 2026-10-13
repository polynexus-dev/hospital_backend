"""Enquiry ageing report: which open enquiries are waiting too long, for
whom, and how quickly the team makes first contact.

GET /api/v1/enquiries/ageing/?department=&assigned_to=&source=&export=csv|xlsx
"""
import csv
import io
from statistics import median

from django.db.models import Max, OuterRef, Q, Subquery
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Enquiry, EnquiryStageChange
from .services import OPEN_STAGES

AGE_BUCKETS = [("under_4h", 4), ("4_24h", 24), ("1_3d", 72), ("3_7d", 168), ("7_30d", 720), ("over_30d", None)]
QUIET_BUCKETS = [("today", 24), ("1_3d", 72), ("3_7d", 168), ("over_7d", None)]
ROW_LIMIT = 300


def _bucket(hours, buckets):
    for key, limit in buckets:
        if limit is None or hours < limit:
            return key
    return buckets[-1][0]


def _scoped(request):
    qs = Enquiry.objects.filter(hospital_id=request.user.hospital_id, stage__in=OPEN_STAGES, duplicate_of__isnull=True)
    role = getattr(request.user, "role", None)
    if role is not None and getattr(role, "data_scope", "all") == "assigned_only":
        qs = qs.filter(assigned_to=request.user)
    p = request.query_params
    if p.get("department"):
        qs = qs.filter(department_id=p["department"])
    if p.get("assigned_to"):
        qs = qs.filter(assigned_to_id=None if p["assigned_to"] == "none" else p["assigned_to"])
    if p.get("source"):
        qs = qs.filter(source=p["source"])
    return qs


def build(request):
    now = timezone.now()
    today = timezone.localdate()
    last_stage = EnquiryStageChange.objects.filter(enquiry=OuterRef("pk")).order_by("-created_at").values("created_at")[:1]
    qs = (_scoped(request).select_related("assigned_to", "department")
          .annotate(last_stage_at=Subquery(last_stage), last_call_at=Max("calls__started_at"), last_message_at=Max("messages__created_at")))

    rows, by_owner = [], {}
    age_counts = {k: 0 for k, _ in AGE_BUCKETS}
    quiet_counts = {k: 0 for k, _ in QUIET_BUCKETS}
    breached = overdue_follow_ups = untouched = 0
    for e in qs:
        activity = max(t for t in (e.created_at, e.last_stage_at, e.last_call_at, e.last_message_at) if t)
        age_h = (now - e.created_at).total_seconds() / 3600
        quiet_h = (now - activity).total_seconds() / 3600
        is_breached = e.stage == Enquiry.Stage.NEW and e.sla_due_at is not None and e.sla_due_at < now
        follow_up_overdue = bool(e.follow_up_date and e.follow_up_date < today and e.stage != Enquiry.Stage.NEW)
        age_counts[_bucket(age_h, AGE_BUCKETS)] += 1
        quiet_counts[_bucket(quiet_h, QUIET_BUCKETS)] += 1
        breached += is_breached
        overdue_follow_ups += follow_up_overdue
        untouched += e.stage == Enquiry.Stage.NEW
        owner_key = e.assigned_to_id or 0
        o = by_owner.setdefault(owner_key, {"owner_id": e.assigned_to_id, "owner": (e.assigned_to.get_full_name() or e.assigned_to.email) if e.assigned_to_id else "Unassigned",
                                            "open": 0, "untouched": 0, "sla_breached": 0, "follow_ups_overdue": 0, "oldest_hours": 0})
        o["open"] += 1
        o["untouched"] += e.stage == Enquiry.Stage.NEW
        o["sla_breached"] += is_breached
        o["follow_ups_overdue"] += follow_up_overdue
        o["oldest_hours"] = max(o["oldest_hours"], round(age_h, 1))
        rows.append({
            "id": e.pk, "name": e.name, "source": e.source, "stage": e.stage, "department": e.department.name if e.department_id else "",
            "owner": o["owner"], "created_at": e.created_at, "age_hours": round(age_h, 1), "last_activity_at": activity,
            "quiet_hours": round(quiet_h, 1), "sla_due_at": e.sla_due_at, "sla_breached": is_breached, "escalation_level": e.escalation_level,
            "follow_up_date": e.follow_up_date, "follow_up_overdue": follow_up_overdue,
        })

    # Most urgent first: breached, then overdue follow-ups, then quietest.
    rows.sort(key=lambda r: (not r["sla_breached"], not r["follow_up_overdue"], -r["quiet_hours"]))

    since = now - timezone.timedelta(days=30)
    recent = Enquiry.objects.filter(hospital_id=request.user.hospital_id, created_at__gte=since, first_contacted_at__isnull=False)
    if getattr(getattr(request.user, "role", None), "data_scope", "all") == "assigned_only":
        recent = recent.filter(assigned_to=request.user)
    response_minutes = [(e.first_contacted_at - e.created_at).total_seconds() / 60 for e in recent.only("created_at", "first_contacted_at", "sla_due_at")]
    from .sla import DEFAULT_FIRST_CONTACT_MINUTES

    within = sum(1 for m in response_minutes if m <= DEFAULT_FIRST_CONTACT_MINUTES)
    return {
        "generated_at": now,
        "summary": {
            "open": len(rows), "untouched": untouched, "sla_breached": breached, "follow_ups_overdue": overdue_follow_ups,
            "median_first_response_minutes_30d": round(median(response_minutes)) if response_minutes else None,
            "contacted_within_4h_pct_30d": round(100 * within / len(response_minutes)) if response_minutes else None,
        },
        "by_age": age_counts, "by_quiet_time": quiet_counts,
        "by_owner": sorted(by_owner.values(), key=lambda o: (-o["sla_breached"], -o["untouched"], -o["open"])),
        "rows": rows[:ROW_LIMIT], "rows_truncated": len(rows) > ROW_LIMIT,
    }


EXPORT_COLUMNS = [("id", "Enquiry #"), ("name", "Name"), ("source", "Source"), ("stage", "Stage"), ("department", "Department"), ("owner", "Owner"),
                  ("age_hours", "Age (hours)"), ("quiet_hours", "Hours since last activity"), ("sla_breached", "First-contact SLA breached"),
                  ("escalation_level", "Escalation level"), ("follow_up_date", "Follow-up date"), ("follow_up_overdue", "Follow-up overdue")]


class EnquiryAgeingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.has_perm("enquiries.view_enquiry"):
            return Response({"detail": "No access to enquiries."}, status=403)
        data = build(request)
        fmt = request.query_params.get("export")
        if fmt not in ("csv", "xlsx"):
            return Response(data)
        table = [[label for _, label in EXPORT_COLUMNS]] + [
            ["Yes" if r[k] is True else "No" if r[k] is False else ("" if r[k] is None else str(r[k])) for k, _ in EXPORT_COLUMNS] for r in data["rows"]]
        stamp = timezone.localdate().isoformat()
        if fmt == "xlsx":
            from apps.core.xlsx import build_xlsx

            resp = HttpResponse(build_xlsx(table, "Enquiry ageing"), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            resp["Content-Disposition"] = f'attachment; filename="enquiry-ageing-{stamp}.xlsx"'
            return resp
        buf = io.StringIO()
        csv.writer(buf).writerows(table)
        resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="enquiry-ageing-{stamp}.csv"'
        return resp
