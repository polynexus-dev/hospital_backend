from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.core.permissions import ActionPermissionRequired
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from .models import Attendance, Employee, LeaveRequest, Shift
from .serializers import AttendanceSerializer, EmployeeSerializer, LeaveRequestSerializer, ShiftSerializer
from .signals import employee_onboarded


class EmployeeViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "hr.view_employee",
        "retrieve": "hr.view_employee",
        "create": "hr.add_employee",
        "update": "hr.change_employee",
        "partial_update": "hr.change_employee",
        "link_user": "hr.change_employee",
    }
    serializer_class = EmployeeSerializer
    audited_fields = ("employee_code", "department", "designation", "employment_type")

    def get_queryset(self):
        return Employee.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        emp = serializer.save(hospital=self.request.user.hospital)
        self._log("create", emp)
        employee_onboarded.send(sender=self.__class__, employee=emp)

    @action(detail=True, methods=["post"])
    def link_user(self, request, pk=None):
        employee = self.get_object()
        user_id = request.data.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        employee.user_id = user_id
        employee.save()
        self._log("update", employee)
        employee_onboarded.send(sender=self.__class__, employee=employee)
        return Response(EmployeeSerializer(employee).data)


class AttendanceViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "hr.view_attendance",
        "retrieve": "hr.view_attendance",
        "create": "hr.add_attendance",
        "update": "hr.change_attendance",
        "partial_update": "hr.change_attendance",
    }
    serializer_class = AttendanceSerializer
    audited_fields = ("date", "status")

    def get_queryset(self):
        return Attendance.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        att = serializer.save(hospital=self.request.user.hospital)
        self._log("create", att)


class LeaveRequestViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "hr.view_leaverequest",
        "retrieve": "hr.view_leaverequest",
        "create": "hr.add_leaverequest",
        "update": "hr.change_leaverequest",
        "partial_update": "hr.change_leaverequest",
        "approve": "hr.change_leaverequest",
        "reject": "hr.change_leaverequest",
    }
    serializer_class = LeaveRequestSerializer
    audited_fields = ("leave_type", "start_date", "end_date", "status")

    def get_queryset(self):
        return LeaveRequest.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        leave = serializer.save(hospital=self.request.user.hospital)
        self._log("create", leave)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        leave = self.get_object()
        leave.status = LeaveRequest.Status.APPROVED
        leave.approved_by = request.user
        leave.save()
        self._log("update", leave)
        return Response(LeaveRequestSerializer(leave).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        leave = self.get_object()
        leave.status = LeaveRequest.Status.REJECTED
        leave.approved_by = request.user
        leave.save()
        self._log("update", leave)
        return Response(LeaveRequestSerializer(leave).data)


class ShiftViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, ActionPermissionRequired]
    action_permissions = {
        "list": "hr.view_shift",
        "retrieve": "hr.view_shift",
        "create": "hr.add_shift",
        "update": "hr.change_shift",
        "partial_update": "hr.change_shift",
    }
    serializer_class = ShiftSerializer
    audited_fields = ("shift_date", "shift_type")

    def get_queryset(self):
        return Shift.objects.filter(hospital=self.request.user.hospital)

    def perform_create(self, serializer):
        shift = serializer.save(hospital=self.request.user.hospital)
        self._log("create", shift)
