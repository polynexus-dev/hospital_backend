from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from .models import Expense, Ledger, Receivable

User = get_user_model()


class FinanceComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="Fin Hosp A", slug="fin-hosp-a")
        self.hospital_b = Hospital.objects.create(name="Fin Hosp B", slug="fin-hosp-b")

        self.user_a = User.objects.create_user(email="fin_mgr@hospa.com", password="password123", hospital=self.hospital_a)
        self.user_b = User.objects.create_user(email="fin_mgr@hospb.com", password="password123", hospital=self.hospital_b)

        self.role_fin = Role.objects.create(
            hospital=self.hospital_a,
            name="Finance Manager",
            template=Role.Template.FINANCE_MANAGER,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.user_a, self.role_fin)

        self.exp_a = Expense.objects.create(hospital=self.hospital_a, paid_by=self.user_a, category="Medical Supplies", amount=1500, paid_to="Vendor X")
        self.exp_b = Expense.objects.create(hospital=self.hospital_b, paid_by=self.user_b, category="Stationery", amount=500, paid_to="Vendor Y")

    def test_tenant_isolation_expenses(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.get("/api/v1/finance/expenses/")
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        exp_ids = [e["id"] for e in results]
        self.assertIn(self.exp_a.id, exp_ids)
        self.assertNotIn(self.exp_b.id, exp_ids)

    def test_expense_creation_posts_to_ledger(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.post("/api/v1/finance/expenses/", {
            "category": "Utility",
            "amount": 2500,
            "paid_to": "Electricity Board",
        })
        self.assertEqual(res.status_code, 201)

        exp_id = res.data["id"]
        self.assertTrue(Ledger.objects.filter(reference_id=str(exp_id), entry_type=Ledger.EntryType.EXPENSE).exists())

    def test_expense_approval_workflow(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        res = client.post(f"/api/v1/finance/expenses/{self.exp_a.id}/approve/")
        self.assertEqual(res.status_code, 200)

        self.exp_a.refresh_from_db()
        self.assertEqual(self.exp_a.approved_by, self.user_a)
