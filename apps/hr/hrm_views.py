import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, model_serializer

from .models import Attendance, Employee, LeaveRequest, Shift
from .models_hrm import (
    Appraisal,
    Candidate,
    DutyRule,
    ExitRequest,
    JobOpening,
    PayrollRun,
    Payslip,
    RosterPublication,
    SalaryStructure,
    StaffProfile,
    TrainingAttendance,
    TrainingProgram,
)

_emp = {"employee_name": serializers.CharField(source="employee.user.get_full_name", read_only=True, default=None),
        "employee_code": serializers.CharField(source="employee.employee_code", read_only=True)}


class StaffProfileViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(StaffProfile, extra=_emp)
    queryset = StaffProfile.objects.select_related("employee__user")
    filterset_fields = ["employee", "credentials_verified"]

    @action(detail=False, methods=["get"])
    def expiring_registrations(self, request):
        soon = timezone.localdate() + timedelta(days=int(request.query_params.get("days", 60)))
        return Response(self.get_serializer(self.get_queryset().filter(registration_valid_until__lte=soon), many=True).data)


class DutyRuleViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(DutyRule)
    queryset = DutyRule.objects.all()


def check_duty_rules(hospital_id, start, end):
    """HRM.1.c — violations in the rostered shifts for the period."""
    rules = list(DutyRule.objects.filter(hospital_id=hospital_id, is_active=True))
    if not rules:
        return []
    shifts = Shift.objects.filter(hospital_id=hospital_id, shift_date__range=(start - timedelta(days=6), end)).select_related("employee__user").order_by("employee_id", "shift_date")
    by_emp = defaultdict(list)
    for s in shifts:
        by_emp[s.employee].append(s)
    out = []
    for emp, rows in by_emp.items():
        for rule in rules:
            if rule.applies_to_designation and rule.applies_to_designation.lower() not in (emp.designation or "").lower():
                continue
            name = emp.user.get_full_name() if emp.user_id else emp.employee_code
            d = start
            while d <= end:
                window = [s for s in rows if d - timedelta(days=6) <= s.shift_date <= d]
                if len(window) > rule.max_shifts_per_week:
                    out.append({"employee": name, "rule": rule.name, "date": str(d), "issue": f"{len(window)} shifts in 7 days (max {rule.max_shifts_per_week})"})
                    break
                d += timedelta(days=1)
            nights = 0
            for s in rows:
                nights = nights + 1 if s.shift_type == "night" else 0
                if nights > rule.max_consecutive_nights:
                    out.append({"employee": name, "rule": rule.name, "date": str(s.shift_date), "issue": f"More than {rule.max_consecutive_nights} consecutive nights"})
                    break
            for a, b in zip(rows, rows[1:]):
                if a.shift_type == "night" and b.shift_type == "morning" and (b.shift_date - a.shift_date).days == 1 and rule.min_rest_hours_between_shifts >= 8:
                    out.append({"employee": name, "rule": rule.name, "date": str(b.shift_date), "issue": "Night followed by morning shift — insufficient rest"})
                    break
    return out


class RosterPublicationViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(RosterPublication, read_only=("published_by", "published_at", "violations", "notified_count"))
    queryset = RosterPublication.objects.all()
    actor_field = "published_by"

    def perform_create(self, serializer):
        v = serializer.validated_data
        violations = check_duty_rules(self.request.user.hospital_id, v["period_start"], v["period_end"])
        if violations and not self.request.data.get("override"):
            raise ValidationError({"violations": violations, "detail": "Roster breaks duty rules — fix or publish with override."})
        super().perform_create(serializer)
        pub = serializer.instance
        pub.violations = violations
        # HRM.1.e — each rostered staff member gets their shift list.
        from apps.clinical.models import ClinicalAlert

        shifts = Shift.objects.filter(hospital_id=pub.hospital_id, shift_date__range=(pub.period_start, pub.period_end)).select_related("employee__user")
        if pub.department_id:
            shifts = shifts.filter(employee__department_id=pub.department_id)
        per_user = defaultdict(list)
        for s in shifts:
            if s.employee.user_id:
                per_user[s.employee.user].append(f"{s.shift_date:%a %d %b}: {s.get_shift_type_display()}")
        for user, lines in per_user.items():
            ClinicalAlert.objects.create(hospital_id=pub.hospital_id, alert_type="cdss", severity="info", target_user=user,
                                         title=f"Your roster {pub.period_start:%d %b}–{pub.period_end:%d %b}", message="\n".join(lines), object_id=str(pub.pk))
        pub.notified_count = len(per_user)
        pub.save(update_fields=["violations", "notified_count"])


class SalaryStructureViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SalaryStructure, extra=_emp)
    queryset = SalaryStructure.objects.select_related("employee__user")
    audited_fields = ("basic", "hra", "special_allowance", "other_allowances", "monthly_tds")


class PayrollRunViewSet(TenantCRUDViewSet):
    """HRM.1.i — calculate from salary structure × paid days (attendance
    minus unpaid leave), approve, then share payslips."""

    serializer_class = model_serializer(PayrollRun, read_only=("status", "processed_by", "approved_by", "total_gross", "total_net"))
    queryset = PayrollRun.objects.all()
    actor_field = "processed_by"

    def perform_create(self, serializer):
        month = serializer.validated_data["month"].replace(day=1)
        serializer.validated_data["month"] = month
        with transaction.atomic():
            super().perform_create(serializer)
            run = serializer.instance
            days = calendar.monthrange(month.year, month.month)[1]
            end = month.replace(day=days)
            tg = tn = Decimal("0")
            for st in SalaryStructure.objects.filter(hospital_id=run.hospital_id).select_related("employee"):
                emp = st.employee
                absent = Attendance.objects.filter(employee=emp, date__range=(month, end), status="absent").count()
                unpaid = sum(
                    (min(l.end_date, end) - max(l.start_date, month)).days + 1
                    for l in LeaveRequest.objects.filter(employee=emp, status="approved", leave_type__icontains="unpaid", start_date__lte=end, end_date__gte=month)
                )
                paid_days = max(Decimal(days - absent - unpaid), Decimal("0"))
                calc = Payslip.compute(st, days, paid_days, month)
                Payslip.objects.create(hospital_id=run.hospital_id, run=run, employee=emp, days_in_month=days, paid_days=paid_days, **calc)
                tg += calc["gross"]
                tn += calc["net_pay"]
            run.total_gross, run.total_net = tg, tn
            run.save(update_fields=["total_gross", "total_net"])

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        run = self.get_object()
        if run.processed_by_id == request.user.pk:
            return Response({"detail": "Payroll must be approved by someone other than the preparer."}, status=403)
        run.status = PayrollRun.Status.APPROVED
        run.approved_by = request.user
        run.save(update_fields=["status", "approved_by"])
        return Response(self.get_serializer(run).data)

    @action(detail=True, methods=["post"])
    def share_payslips(self, request, pk=None):
        run = self.get_object()
        if run.status == PayrollRun.Status.DRAFT:
            return Response({"detail": "Approve the payroll first."}, status=400)
        from django.core.mail import send_mail

        n = 0
        for slip in run.payslips.select_related("employee__user"):
            user = slip.employee.user
            if user and user.email:
                send_mail(f"Payslip {run.month:%B %Y}", f"Net pay ₹{slip.net_pay} (gross ₹{slip.gross}, deductions ₹{slip.total_deductions}).",
                          None, [user.email], fail_silently=True)
            slip.shared_at = timezone.now()
            slip.save(update_fields=["shared_at"])
            n += 1
        return Response({"shared": n})

    @action(detail=True, methods=["get"])
    def payslips(self, request, pk=None):
        run = self.get_object()
        return Response(PayslipSerializer(run.payslips.select_related("employee__user"), many=True).data)


class PayslipSerializer(model_serializer(Payslip, extra=_emp)):
    pass


class AppraisalViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(Appraisal, read_only=("overall_rating", "appraiser", "employee_acknowledged_at"), extra=_emp)
    queryset = Appraisal.objects.select_related("employee__user")
    filterset_fields = ["employee", "period"]
    actor_field = "appraiser"

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        a = self.get_object()
        a.employee_acknowledged_at = timezone.now()
        a.save(update_fields=["employee_acknowledged_at"])
        return Response(self.get_serializer(a).data)


class JobOpeningViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(JobOpening, read_only=("approval_status", "approved_by"))
    queryset = JobOpening.objects.all()
    filterset_fields = ["status", "approval_status"]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        o = self.get_object()
        o.approval_status = "approved"
        o.approved_by = request.user
        o.save(update_fields=["approval_status", "approved_by"])
        return Response(self.get_serializer(o).data)


class CandidateViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(Candidate, read_only=("stage",))
    queryset = Candidate.objects.all()
    filterset_fields = ["opening", "stage"]

    def perform_create(self, serializer):
        if serializer.validated_data["opening"].approval_status != "approved":
            raise ValidationError({"opening": "Opening is not approved yet."})
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        c = self.get_object()
        stage = request.data.get("stage")
        if stage not in Candidate.STAGES:
            return Response({"stage": Candidate.STAGES}, status=400)
        if stage == "joined" and not (c.credentials_verified and c.police_verification_done and c.medical_fitness_done):
            return Response({"detail": "Credential, police and medical-fitness verification must be complete before joining."}, status=400)
        if request.data.get("feedback"):
            c.interview_feedback = [*c.interview_feedback, {"by": request.user.get_full_name(), **request.data["feedback"]}]
        c.stage = stage
        c.save()
        return Response(self.get_serializer(c).data)


class ExitRequestViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ExitRequest, read_only=("status",), extra=_emp)
    queryset = ExitRequest.objects.select_related("employee__user")
    filterset_fields = ["employee", "status"]

    def perform_create(self, serializer):
        serializer.validated_data.setdefault("clearances", {})
        for k in ExitRequest.DEFAULT_CLEARANCES:
            serializer.validated_data["clearances"].setdefault(k, False)
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def clear(self, request, pk=None):
        e = self.get_object()
        dept = request.data.get("department")
        if dept not in e.clearances:
            return Response({"department": list(e.clearances)}, status=400)
        e.clearances[dept] = True
        if all(e.clearances.values()):
            e.status = "cleared"
        e.save()
        return Response(self.get_serializer(e).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        e = self.get_object()
        if e.status != "cleared":
            return Response({"detail": "All clearances are pending."}, status=400)
        e.status = "completed"
        e.exit_interview_notes = request.data.get("exit_interview_notes", e.exit_interview_notes)
        e.full_and_final_amount = request.data.get("full_and_final_amount", e.full_and_final_amount)
        e.save()
        if e.employee.user_id:
            e.employee.user.is_active = False
            e.employee.user.save(update_fields=["is_active"])
        return Response(self.get_serializer(e).data)


class TrainingProgramSerializer(model_serializer(TrainingProgram)):
    attendance_summary = serializers.SerializerMethodField()

    def get_attendance_summary(self, obj):
        rows = list(obj.attendance.all())
        fb = [r.feedback_rating for r in rows if r.feedback_rating]
        return {"invited": obj.invitees.count(), "attended": sum(1 for r in rows if r.attended), "avg_feedback": round(sum(fb) / len(fb), 2) if fb else None}


class TrainingProgramViewSet(TenantCRUDViewSet):
    serializer_class = TrainingProgramSerializer
    queryset = TrainingProgram.objects.prefetch_related("attendance", "invitees")
    filterset_fields = ["kind"]

    @action(detail=False, methods=["get"])
    def calendar(self, request):
        start = request.query_params.get("start") or str(timezone.localdate())
        qs = self.get_queryset().filter(scheduled_at__date__gte=start)[:100]
        return Response(self.get_serializer(qs, many=True).data)

    @action(detail=True, methods=["post"])
    def record(self, request, pk=None):
        prog = self.get_object()
        for row in request.data.get("attendees") or []:
            TrainingAttendance.objects.update_or_create(
                hospital_id=prog.hospital_id, program=prog, employee_id=row["employee"],
                defaults={k: row.get(k) for k in ("attended", "pre_test_score", "post_test_score", "feedback_rating", "feedback") if k in row},
            )
        return Response(self.get_serializer(prog).data)

    @action(detail=False, methods=["get"])
    def pending_induction(self, request):
        """HRM.3.a — staff who joined in the last 90 days without an induction."""
        since = timezone.localdate() - timedelta(days=90)
        done = TrainingAttendance.objects.filter(hospital_id=request.user.hospital_id, program__kind="induction", attended=True).values_list("employee_id", flat=True)
        rows = Employee.objects.filter(hospital_id=request.user.hospital_id, date_of_joining__gte=since).exclude(pk__in=done).select_related("user")
        return Response([{"id": e.pk, "employee_code": e.employee_code, "name": e.user.get_full_name() if e.user_id else "", "joined": e.date_of_joining} for e in rows])
