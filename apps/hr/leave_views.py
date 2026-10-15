"""Leave types, balances (with year-end carry forward), staff dependents
and overtime."""
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, model_serializer

from . import leave
from .models import Employee, EmployeeDependent, LeaveBalance, LeaveType, OvertimeEntry
from .staff_rate import staff_link


def _employee_name(emp):
    return (emp.user.get_full_name() if emp.user_id else "") or emp.designation


_emp = {
    "employee_code": serializers.CharField(source="employee.employee_code", read_only=True),
    "employee_name": serializers.SerializerMethodField(),
}


class _EmpNameMixin:
    def get_employee_name(self, obj):
        return _employee_name(obj.employee)


class LeaveTypeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(LeaveType)
    queryset = LeaveType.objects.all()
    filterset_fields = ["is_active", "is_paid"]
    search_fields = ["code", "name"]
    audited_fields = ("annual_quota", "accrual", "carry_forward_max", "when_short", "is_paid", "is_active")


def _year(request):
    try:
        return int(request.query_params.get("year") or request.data.get("year") or timezone.localdate().year)
    except (TypeError, ValueError):
        raise ValidationError({"year": "A year, e.g. 2026."})


class LeaveBalanceSerializer(_EmpNameMixin, model_serializer(LeaveBalance, read_only=("carried_forward_at",), extra={
    **_emp, "leave_type_name": serializers.CharField(source="leave_type.name", read_only=True),
})):
    pass


class LeaveBalanceViewSet(TenantCRUDViewSet):
    """Stored opening / adjustment rows; `summary` gives the full balance."""

    serializer_class = LeaveBalanceSerializer
    queryset = LeaveBalance.objects.select_related("employee__user", "leave_type")
    filterset_fields = ["employee", "leave_type", "year"]
    audited_fields = ("opening", "adjustment", "note")

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """GET ?employee=&year= — every active leave type's balance; without employee, all employees."""
        year = _year(request)
        emps = Employee.objects.filter(hospital_id=request.user.hospital_id).select_related("user")
        if request.query_params.get("employee"):
            emps = emps.filter(pk=request.query_params["employee"])
        return Response([
            {"employee": e.pk, "employee_code": e.employee_code, "employee_name": _employee_name(e), "year": year, "balances": leave.summary(e, year)}
            for e in emps
        ])

    @action(detail=False, methods=["post"], url_path="carry-forward")
    def carry_forward(self, request):
        """POST {year} — close the year: unused days (capped per type) become next year's opening."""
        year = _year(request)
        if year >= timezone.localdate().year:
            return Response({"year": "Only a finished year can be carried forward."}, status=status.HTTP_400_BAD_REQUEST)
        n = leave.carry_forward(request.user.hospital_id, year)
        return Response({"year": year, "into": year + 1, "balances_set": n})


class DependentSerializer(_EmpNameMixin, model_serializer(EmployeeDependent, extra={
    **_emp,
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
})):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        emp = attrs.get("employee", getattr(self.instance, "employee", None))
        patient = attrs.get("patient", getattr(self.instance, "patient", None))
        if emp is not None and patient is not None and emp.patient_id == patient.pk:
            raise serializers.ValidationError({"patient": "That's the employee's own patient record."})
        return attrs


class EmployeeDependentViewSet(TenantCRUDViewSet):
    serializer_class = DependentSerializer
    queryset = EmployeeDependent.objects.select_related("employee__user", "patient")
    filterset_fields = ["employee", "patient", "is_active"]
    audited_fields = ("relationship", "valid_until", "is_active")

    # Any signed-in user of the hospital may ask: the front desk and billing
    # need to see why a patient gets the staff rate, and they hold no HR role.
    @action(detail=False, methods=["get"], url_path="staff-link", permission_classes=[IsAuthenticated])
    def staff_link(self, request):
        """GET ?patient= — is this patient staff or a staff dependent (and so billed at the staff rate)?"""
        from apps.patients.models import Patient

        patient = Patient.objects.filter(hospital_id=request.user.hospital_id, pk=request.query_params.get("patient") or 0).first()
        if patient is None:
            return Response({"patient": "Unknown patient."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"patient": patient.pk, "link": staff_link(patient)})


class OvertimeSerializer(_EmpNameMixin, model_serializer(OvertimeEntry, read_only=("status", "decided_by", "decided_at"), extra=_emp)):
    def validate_hours(self, value):
        if value <= 0 or value > 16:
            raise serializers.ValidationError("Between 0 and 16 hours.")
        return value


class OvertimeEntryViewSet(TenantCRUDViewSet):
    serializer_class = OvertimeSerializer
    queryset = OvertimeEntry.objects.select_related("employee__user")
    filterset_fields = ["employee", "status", "date"]
    audited_fields = ("hours", "status")

    def perform_update(self, serializer):
        if serializer.instance.status != OvertimeEntry.Status.PENDING:
            raise ValidationError({"detail": "Decided overtime can't be changed."})
        super().perform_update(serializer)

    def _decide(self, request, decision):
        entry = self.get_object()
        if entry.status != OvertimeEntry.Status.PENDING:
            return Response({"detail": "Already decided."}, status=status.HTTP_400_BAD_REQUEST)
        if entry.employee.user_id == request.user.pk:
            return Response({"detail": "Overtime must be approved by someone else."}, status=status.HTTP_403_FORBIDDEN)
        entry.status, entry.decided_by, entry.decided_at = decision, request.user, timezone.now()
        entry.save(update_fields=["status", "decided_by", "decided_at"])
        self._log("update", entry)
        return Response(self.get_serializer(entry).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        return self._decide(request, OvertimeEntry.Status.APPROVED)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._decide(request, OvertimeEntry.Status.REJECTED)
