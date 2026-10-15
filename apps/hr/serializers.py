from rest_framework import serializers
from apps.accounts.serializers import UserSerializer
from apps.facilities.serializers import DepartmentSerializer
from .models import Attendance, Employee, LeaveRequest, Shift


class EmployeeSerializer(serializers.ModelSerializer):
    user_detail = UserSerializer(source="user", read_only=True)
    department_detail = DepartmentSerializer(source="department", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True, default="")

    class Meta:
        model = Employee
        fields = [
            "id",
            "user",
            "user_detail",
            "employee_code",
            "name_prefix",
            "marital_status",
            "department",
            "department_detail",
            "designation",
            "date_of_joining",
            "employment_type",
            "bank_account_number",
            "pan_number",
            "patient",
            "patient_name",
        ]
        read_only_fields = ["id"]


    def validate_patient(self, patient):
        user = self.context["request"].user if self.context.get("request") else None
        if patient is not None and user is not None and patient.hospital_id != user.hospital_id:
            raise serializers.ValidationError("Refers to a record from a different hospital.")
        return patient


class AttendanceSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")

    class Meta:
        model = Attendance
        fields = ["id", "employee", "employee_code", "date", "check_in", "check_out", "status"]
        read_only_fields = ["id"]


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")
    approved_by_name = serializers.ReadOnlyField(source="approved_by.get_full_name")

    def validate(self, attrs):
        hospital_id = getattr(self.context.get("request").user, "hospital_id", None) if self.context.get("request") else None
        for name in ("employee", "leave_type_master"):
            obj = attrs.get(name)
            if obj is not None and hospital_id is not None and obj.hospital_id != hospital_id:
                raise serializers.ValidationError({name: "Refers to a record from a different hospital."})
        if not attrs.get("leave_type_master") and not attrs.get("leave_type") and self.instance is None:
            raise serializers.ValidationError({"leave_type_master": "Choose the leave type."})
        return attrs

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "employee",
            "employee_code",
            "leave_type",
            "leave_type_master",
            "start_date",
            "end_date",
            "half_day",
            "days",
            "reason",
            "short_balance",
            "status",
            "approved_by",
            "approved_by_name",
        ]
        read_only_fields = ["id", "days", "short_balance", "status", "approved_by"]


class ShiftSerializer(serializers.ModelSerializer):
    employee_code = serializers.ReadOnlyField(source="employee.employee_code")

    class Meta:
        model = Shift
        fields = ["id", "employee", "employee_code", "shift_date", "shift_type"]
        read_only_fields = ["id"]
