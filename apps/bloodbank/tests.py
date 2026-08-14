from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.patients.models import Patient
from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion

User = get_user_model()


class BloodBankComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="BB Hospital A", slug="bb-hosp-a")
        self.user_a = User.objects.create_user(email="bb_staff@hospa.com", password="password123", hospital=self.hospital_a)
        
        self.role_bb = Role.objects.create(
            hospital=self.hospital_a,
            name="Admin Role",
            template=Role.Template.HOSPITAL_ADMINISTRATOR,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.user_a, self.role_bb)

        self.patient_a = Patient.objects.create(hospital=self.hospital_a, first_name="Clara", last_name="Barton", mobile="9876543211")
        self.donor_a = Donor.objects.create(hospital=self.hospital_a, name="John Donor", blood_group="O+", phone="9112233445")
        
        today = timezone.localdate()
        self.unit_a = BloodUnit.objects.create(
            hospital=self.hospital_a,
            donor=self.donor_a,
            blood_group="O+",
            component=BloodUnit.Component.PRBC,
            collection_date=today,
            expiry_date=today + timezone.timedelta(days=35),
            status=BloodUnit.Status.AVAILABLE,
        )

    def test_donor_and_unit_creation(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)
        
        d_res = client.post("/api/v1/bloodbank/donors/", {
            "name": "Jane Donor",
            "blood_group": "A+",
            "phone": "9887766554",
        })
        self.assertEqual(d_res.status_code, 201)

        u_res = client.post("/api/v1/bloodbank/units/", {
            "blood_group": "A+",
            "component": "prbc",
            "collection_date": "2026-08-14",
            "expiry_date": "2026-09-18",
        })
        self.assertEqual(u_res.status_code, 201)

    def test_transfusion_auto_updates_unit_status(self):
        client = APIClient()
        client.force_authenticate(user=self.user_a)

        t_res = client.post("/api/v1/bloodbank/transfusions/", {
            "blood_unit": self.unit_a.id,
            "patient": self.patient_a.id,
            "reaction_notes": "Uneventful transfusion. Vitals stable.",
        })
        self.assertEqual(t_res.status_code, 201)

        self.unit_a.refresh_from_db()
        self.assertEqual(self.unit_a.status, BloodUnit.Status.ISSUED)
