from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.accounts.models import Role, assign_role
from apps.core.models import Hospital
from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission
from apps.patients.models import Patient
from .models import ICUAdmission, ICUDailyProgressNote, VentilatorLog

User = get_user_model()


class ICUComprehensiveTestCase(TestCase):
    def setUp(self):
        self.hospital_a = Hospital.objects.create(name="ICU Hospital A", slug="icu-hosp-a")
        
        self.doctor_user_a = User.objects.create_user(email="icu_doc@hospa.com", password="password123", hospital=self.hospital_a)
        self.role_icu = Role.objects.create(
            hospital=self.hospital_a,
            name="ICU Staff Role",
            template=Role.Template.ICU_STAFF,
            data_scope=Role.DataScope.ALL,
        )
        assign_role(self.doctor_user_a, self.role_icu)

        self.doctor_a = Doctor.objects.create(hospital=self.hospital_a, user=self.doctor_user_a, name="Dr. Intensivist")

        self.patient_a = Patient.objects.create(hospital=self.hospital_a, first_name="Bram", last_name="Stoker", mobile="9123456780")
        self.ward_a = Ward.objects.create(hospital=self.hospital_a, name="ICU Ward")
        self.room_a = Room.objects.create(hospital=self.hospital_a, ward=self.ward_a, room_number="ICU-R1")
        self.bed_a = Bed.objects.create(hospital=self.hospital_a, room=self.room_a, bed_number="ICU-01")

        self.admission_a = Admission.objects.create(
            hospital=self.hospital_a,
            patient=self.patient_a,
            admitting_doctor=self.doctor_a,
            bed=self.bed_a,
            admission_type=Admission.AdmissionType.EMERGENCY,
            status=Admission.Status.ADMITTED,
            admission_diagnosis="Respiratory Distress",
        )

        self.icu_adm = ICUAdmission.objects.create(
            hospital=self.hospital_a,
            admission=self.admission_a,
            bed=self.bed_a,
            ventilator_required=True,
        )

    def test_ventilator_log_logging(self):
        client = APIClient()
        client.force_authenticate(user=self.doctor_user_a)
        res = client.post("/api/v1/icu/ventilator-logs/", {
            "icu_admission": self.icu_adm.id,
            "mode": "AC/VC",
            "ventilator_settings": {"PEEP": 8, "FiO2": 60},
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertTrue(VentilatorLog.objects.filter(icu_admission=self.icu_adm).exists())

    def test_icu_progress_note_finalization_lock(self):
        client = APIClient()
        client.force_authenticate(user=self.doctor_user_a)

        note = ICUDailyProgressNote.objects.create(
            hospital=self.hospital_a,
            icu_admission=self.icu_adm,
            doctor=self.doctor_a,
            note="Patient hemodynamically stable on low-dose vasopressors.",
        )

        fin_res = client.post(f"/api/v1/icu/progress-notes/{note.id}/finalize/")
        self.assertEqual(fin_res.status_code, 200)

        note.refresh_from_db()
        note.note = "Tampered note string"
        with self.assertRaises(Exception):
            note.save()
