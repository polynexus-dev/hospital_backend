from rest_framework import serializers
from apps.accounts.serializers import UserSerializer
from apps.facilities.serializers import DepartmentSerializer
from .models import Attendance, Employee, LeaveRequest, Shift


class EmployeeSerializer(serializers.ModelSerializer):
    user_detail = UserSerializer(source="user", read_only=True)
    department_detail = DepartmentSerializer(source="department", read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "user",
            "user_detail",
            "employee_code",
            "department",
            "department_detail",
            "designation",
            "date_of_joining",
            "employment_type",
            "bank_account_number",
            "pan_number",
        ]
        read_only_fields = ["id"]


class AttendanceSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")

    class Meta:
        model = Attendance
        fields = ["id", "employee", "employee_code", "date", "check_in", "check_out", "status"]
        read_only_fields = ["id"]


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")
    approved_by_name = serializers.ReadOnlyField(source="approved_by.get_full_name")

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "employee_code",
            "leave_type",
            "start_date",
            "end_date",
            "status",
            "approved_by",
            "approved_by_name",
        ]
        read_only_fields = ["id"]


class ShiftSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")

    class Meta:
        model = Shift
        fields = ["id", "employee", "employee_code", "shift_date", "shift_type"]
        read_only_fields = ["id"]
