from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.facilities.models import Department
from .models import Attendance, Employee, LeaveRequest, Shift

User = get_user_model()


class HRComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="HR Hosp A", slug="hr-hosp-a")
        self.hospital_b = Hospital.objects.create(name="HR Hosp B", slug="hr-hosp-b")

        self.user_a = User.objects.create_user(email="hr_mgr@hospa.com", password="password123", hospital=self.hospital_a)
        self.staff_user_a = User.objects.create_user(email="staff@hospa.com", password="password123", hospital=self.hospital_a)

        self.role_hr = Role.objects.create(
            hospital=self.hospital_a,
            name="HR Manager",
            template=Role.Template.HR_MANAGER,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.user_a, self.role_hr)

        self.dept_a = Department.objects.create(hospital=self.hospital_a, name="Nursing", code="NURS")
        self.dept_b = Department.objects.create(hospital=self.hospital_b, name="Admin", code="ADM")

        self.emp_a = Employee.objects.create(hospital=self.hospital_a, employee_code="EMP-001", department=self.dept_a, designation="Staff Nurse")
        self.emp_b = Employee.objects.create(hospital=self.hospital_b, employee_code="EMP-999", department=self.dept_b, designation="Clerk")

    def test_tenant_isolation_employees(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.get("/api/v1/hr/employees/")
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        emp_ids = [e["id"] for e in results]
        self.assertIn(self.emp_a.id, emp_ids)
        self.assertNotIn(self.emp_b.id, emp_ids)

    def test_link_user_to_employee(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.post(f"/api/v1/hr/employees/{self.emp_a.id}/link_user/", {
            "user_id": self.staff_user_a.id,
        })
        self.assertEqual(res.status_code, 200)

        self.emp_a.refresh_from_db()
        self.assertEqual(self.emp_a.user, self.staff_user_a)

    def test_leave_approval_workflow(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        today = timezone.localdate()
        leave = LeaveRequest.objects.create(
            hospital=self.hospital_a,
            employee=self.emp_a,
            leave_type="Casual",
            start_date=today,
            end_date=today + timezone.timedelta(days=2),
        )

        app_res = client.post(f"/api/v1/hr/leave-requests/{leave.id}/approve/")
        self.assertEqual(app_res.status_code, 200)

        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.approved_by, self.user_a)
