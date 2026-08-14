from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.patients.models import Patient
from .models import Bill, InsuranceClaim, Payment

User = get_user_model()


class BillingComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="Billing Hosp A", slug="bill-hosp-a")
        self.hospital_b = Hospital.objects.create(name="Billing Hosp B", slug="bill-hosp-b")

        self.user_a = User.objects.create_user(email="billing_exec@hospa.com", password="password123", hospital=self.hospital_a)
        self.user_b = User.objects.create_user(email="billing_exec@hospb.com", password="password123", hospital=self.hospital_b)

        self.role_billing = Role.objects.create(
            hospital=self.hospital_a,
            name="Billing Executive",
            template=Role.Template.BILLING_EXECUTIVE,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.user_a, self.role_billing)

        self.patient_a = Patient.objects.create(hospital=self.hospital_a, first_name="Bruce", last_name="Wayne", mobile="9123123123")
        self.patient_b = Patient.objects.create(hospital=self.hospital_b, first_name="Clark", last_name="Kent", mobile="9321321321")

        self.bill_a = Bill.objects.create(hospital=self.hospital_a, patient=self.patient_a, total_amount=1000, net_amount=1000)
        self.bill_b = Bill.objects.create(hospital=self.hospital_b, patient=self.patient_b, total_amount=2000, net_amount=2000)

    def test_tenant_isolation(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.get("/api/v1/billing/bills/")
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        bill_ids = [b["id"] for b in results]
        self.assertIn(self.bill_a.id, bill_ids)
        self.assertNotIn(self.bill_b.id, bill_ids)

    def test_add_bill_item_recalculates_totals(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.post(f"/api/v1/billing/bills/{self.bill_a.id}/add-item/", {
            "description": "Consultation Fee",
            "quantity": 1,
            "unit_price": 500,
        })
        self.assertEqual(res.status_code, 201)

        self.bill_a.refresh_from_db()
        self.assertEqual(float(self.bill_a.total_amount), 500.0)

    def test_payment_and_auto_paid_status(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.post("/api/v1/billing/payments/", {
            "bill": self.bill_a.id,
            "amount": 1000,
            "payment_method": "upi",
        })
        self.assertEqual(res.status_code, 201)

        self.bill_a.refresh_from_db()
        self.assertEqual(self.bill_a.status, Bill.Status.PAID)
