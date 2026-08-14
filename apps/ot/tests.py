from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.appointments.models import Doctor
from apps.patients.models import Patient
from .models import AnaesthesiaRecord, ConsumableUsage, ImplantUsage, OperativeNote, OTSchedule, PreOpChecklist, SurgeryRequest

User = get_user_model()


class OTComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="OT Hospital A", slug="ot-hosp-a")
        self.hospital_b = Hospital.objects.create(name="OT Hospital B", slug="ot-hosp-b")

        self.surgeon_user_a = User.objects.create_user(email="surgeon_a@hospa.com", password="password123", hospital=self.hospital_a)
        self.surgeon_user_b = User.objects.create_user(email="surgeon_b@hospb.com", password="password123", hospital=self.hospital_b)

        self.role_surgeon = Role.objects.create(
            hospital=self.hospital_a,
            name="Surgeon Role",
            template=Role.Template.SURGEON,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.surgeon_user_a, self.role_surgeon)

        self.doctor_a = Doctor.objects.create(hospital=self.hospital_a, user=self.surgeon_user_a, name="Dr. Surgeon A")

        self.patient_a = Patient.objects.create(hospital=self.hospital_a, first_name="Arthur", last_name="Conan", mobile="9876543210")
        self.surg_req_a = SurgeryRequest.objects.create(hospital=self.hospital_a, patient=self.patient_a, proposed_procedure="Appendectomy")

        start = timezone.now()
        end = start + timezone.timedelta(hours=2)
        self.schedule_a = OTSchedule.objects.create(
            hospital=self.hospital_a,
            surgery_request=self.surg_req_a,
            operation_theatre_room="OT-1",
            surgeon=self.doctor_a,
            scheduled_start=start,
            scheduled_end=end,
        )

    def test_preop_checklist_creation(self):
        client = APIClient()
        client.force_authenticate(user=self.surgeon_user_a)
        res = client.post("/api/v1/ot/preop-checklists/", {
            "surgery_request": self.surg_req_a.id,
            "consent_obtained": True,
            "fasting_confirmed": True,
            "site_marked": True,
        })
        self.assertEqual(res.status_code, 201)
        self.assertTrue(PreOpChecklist.objects.filter(surgery_request=self.surg_req_a).exists())

    def test_operative_note_finalization_lock(self):
        client = APIClient()
        client.force_authenticate(user=self.surgeon_user_a)
        
        now = timezone.now()
        note = OperativeNote.objects.create(
            hospital=self.hospital_a,
            ot_schedule=self.schedule_a,
            procedure_performed="Laparoscopic Appendectomy",
            findings="Inflamed appendix",
            surgeon=self.doctor_a,
            started_at=now,
            ended_at=now + timezone.timedelta(hours=1),
        )

        fin_res = client.post(f"/api/v1/ot/operative-notes/{note.id}/finalize/")
        self.assertEqual(fin_res.status_code, 200)
        self.assertIsNotNone(fin_res.data["finalized_at"])

        note.refresh_from_db()
        note.procedure_performed = "Altered Procedure"
        with self.assertRaises(Exception):
            note.save()

    def test_consumable_and_implant_logging(self):
        client = APIClient()
        client.force_authenticate(user=self.surgeon_user_a)
        
        c_res = client.post("/api/v1/ot/consumables/", {
            "ot_schedule": self.schedule_a.id,
            "item_name": "Vicryl 2-0 Sutures",
            "quantity": 3,
        })
        self.assertEqual(c_res.status_code, 201)

        i_res = client.post("/api/v1/ot/implants/", {
            "ot_schedule": self.schedule_a.id,
            "implant_name": "Surgical Mesh",
            "serial_number": "SN-98765",
            "quantity": 1,
        })
        self.assertEqual(i_res.status_code, 201)
