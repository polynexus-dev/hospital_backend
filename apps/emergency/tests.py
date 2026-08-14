from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.patients.models import Patient
from .models import EDVisit, Triage

User = get_user_model()


class EmergencyComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="Hospital A", slug="hosp-a")
        self.hospital_b = Hospital.objects.create(name="Hospital B", slug="hosp-b")

        self.doctor_a = User.objects.create_user(email="doca@hospa.com", password="password123", hospital=self.hospital_a)
        self.doctor_b = User.objects.create_user(email="docb@hospb.com", password="password123", hospital=self.hospital_b)
        self.unauth_user = User.objects.create_user(email="unauth@hospa.com", password="password123", hospital=self.hospital_a)

        self.role_doc_a = Role.objects.create(
            hospital=self.hospital_a,
            name="Doctor A",
            template=Role.Template.DOCTOR,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.doctor_a, self.role_doc_a)

        self.patient_a = Patient.objects.create(hospital=self.hospital_a, first_name="John", last_name="Doe", mobile="9998887771")
        self.patient_b = Patient.objects.create(hospital=self.hospital_b, first_name="Jane", last_name="Smith", mobile="9998887772")

        self.ed_visit_a = EDVisit.objects.create(hospital=self.hospital_a, patient=self.patient_a, chief_complaint="Severe Chest Pain")
        self.ed_visit_b = EDVisit.objects.create(hospital=self.hospital_b, patient=self.patient_b, chief_complaint="Fracture")

    def test_tenant_isolation_ed_visits(self):
        client = APIClient()
        client.force_authenticate(user=self.doctor_a)
        res = client.get("/api/v1/emergency/ed-visits/")
        self.assertEqual(res.status_code, 200)
        results = res.data["results"] if isinstance(res.data, dict) else res.data
        visit_ids = [v["id"] for v in results]
        self.assertIn(self.ed_visit_a.id, visit_ids)
        self.assertNotIn(self.ed_visit_b.id, visit_ids)

    def test_rbac_403_unauthorized_role(self):
        client = APIClient()
        client.force_authenticate(user=self.unauth_user)
        res = client.get("/api/v1/emergency/ed-visits/")
        self.assertEqual(res.status_code, 403)

    def test_triage_creation_and_audit(self):
        client = APIClient()
        client.force_authenticate(user=self.doctor_a)
        res = client.post("/api/v1/emergency/triages/", {
            "ed_visit": self.ed_visit_a.id,
            "triage_category": "1_resuscitation",
            "vitals_summary": "BP 80/50, SpO2 88%",
        })
        self.assertEqual(res.status_code, 201)
        self.assertTrue(Triage.objects.filter(ed_visit=self.ed_visit_a).exists())

    def test_admit_to_ipd_validation(self):
        client = APIClient()
        client.force_authenticate(user=self.doctor_a)
        res = client.post(f"/api/v1/emergency/ed-visits/{self.ed_visit_a.id}/admit-to-ipd/", {})
        self.assertEqual(res.status_code, 400)
