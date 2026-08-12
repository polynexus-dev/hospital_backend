import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Appointment, Doctor, Slot, SlotTemplate
from apps.appointments.services import generate_slots
from apps.automation.models import EscalationRule, Task
from apps.communications.models import Channel, Message, Template
from apps.core.models import Department, Hospital
from apps.enquiries.models import Enquiry
from apps.feedback.models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask
from apps.patients.models import Document, Patient, TimelineEvent
from apps.telephony.models import Call, CallbackTask


class Command(BaseCommand):
    help = "Seeds comprehensive demo data for Hospital CRM (Owner, Front Desk, Doctor, Operator users, Patients, Appointments, Calls, Enquiries, NPS, & Messages)."

    def add_arguments(self, parser):
        parser.add_argument("--admin-password", default="changeme123", help="Password for all seeded demo users.")

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["admin_password"]

        # 1. Hospital Tenant
        hospital, _ = Hospital.objects.get_or_create(
            slug="demo-hospital",
            defaults={
                "name": "Polynexus Demo Hospital",
                "city": "Pune",
                "state": "Maharashtra",
                "address": "Baner Road, Pune, Maharashtra 411045",
                "primary_language": "mr",
                "google_review_url": "https://g.page/r/polynexus-demo-hospital/review",
                "owner_mis_whatsapp_number": "+919876543210",
            },
        )

        # 2. Departments
        opd, _ = Department.objects.get_or_create(hospital=hospital, name="OPD", defaults={"code": "OPD"})
        cardiology, _ = Department.objects.get_or_create(hospital=hospital, name="Cardiology", defaults={"code": "CARD"})
        ortho, _ = Department.objects.get_or_create(hospital=hospital, name="Orthopedics", defaults={"code": "ORTHO"})
        diag, _ = Department.objects.get_or_create(hospital=hospital, name="Diagnostics", defaults={"code": "DIAG"})

        # 3. Roles
        owner_role, _ = Role.objects.get_or_create(hospital=hospital, name="Hospital Owner / Admin")
        front_desk_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="Front Desk Officer")
        doctor_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="OPD Doctor")
        operator_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="Telephony Operator")

        # 4. Demo Users
        # Owner User
        owner_user, created = User.objects.get_or_create(
            email="owner@demo-hospital.example",
            defaults={
                "hospital": hospital,
                "is_staff": True,
                "is_superuser": True,
                "first_name": "Vikram",
                "last_name": "Patil (Owner)",
                "preferred_language": "mr",
            },
        )
        if created or not owner_user.check_password(password):
            owner_user.set_password(password)
            owner_user.save()
        assign_role(owner_user, owner_role)

        # Superuser Admin
        admin_user, created = User.objects.get_or_create(
            email="admin@demo-hospital.example",
            defaults={
                "hospital": hospital,
                "is_staff": True,
                "is_superuser": True,
                "first_name": "System",
                "last_name": "Admin",
                "preferred_language": "en",
            },
        )
        if created or not admin_user.check_password(password):
            admin_user.set_password(password)
            admin_user.save()
        assign_role(admin_user, owner_role)

        # Front Desk User
        frontdesk_user, created = User.objects.get_or_create(
            email="frontdesk@demo-hospital.example",
            defaults={
                "hospital": hospital,
                "department": opd,
                "first_name": "Priya",
                "last_name": "Sharma (Reception)",
                "preferred_language": "mr",
            },
        )
        if created or not frontdesk_user.check_password(password):
            frontdesk_user.set_password(password)
            frontdesk_user.save()
        assign_role(frontdesk_user, front_desk_role)

        # Doctor User
        doctor_user, created = User.objects.get_or_create(
            email="doctor@demo-hospital.example",
            defaults={
                "hospital": hospital,
                "department": opd,
                "first_name": "Dr. Ramesh",
                "last_name": "Kulkarni",
                "preferred_language": "mr",
            },
        )
        if created or not doctor_user.check_password(password):
            doctor_user.set_password(password)
            doctor_user.save()
        assign_role(doctor_user, doctor_role)

        # Telephony Operator User
        operator_user, created = User.objects.get_or_create(
            email="operator@demo-hospital.example",
            defaults={
                "hospital": hospital,
                "department": opd,
                "first_name": "Amit",
                "last_name": "Deshmukh (Call Center)",
                "preferred_language": "hi",
            },
        )
        if created or not operator_user.check_password(password):
            operator_user.set_password(password)
            operator_user.save()
        assign_role(operator_user, operator_role)

        # 5. Doctors
        doc_kulkarni, _ = Doctor.objects.get_or_create(
            hospital=hospital,
            name="Dr. Ramesh Kulkarni",
            defaults={"speciality": "General Medicine", "department": opd, "phone": "+919822011111", "email": "dr.kulkarni@demo-hospital.example"},
        )
        doc_joshi, _ = Doctor.objects.get_or_create(
            hospital=hospital,
            name="Dr. Anjali Joshi",
            defaults={"speciality": "Cardiology", "department": cardiology, "phone": "+919822022222", "email": "dr.joshi@demo-hospital.example"},
        )
        doc_sharma, _ = Doctor.objects.get_or_create(
            hospital=hospital,
            name="Dr. Rajesh Sharma",
            defaults={"speciality": "Orthopedics", "department": ortho, "phone": "+919822033333", "email": "dr.sharma@demo-hospital.example"},
        )

        # 6. Slot Templates & Slot Generation
        for doc in [doc_kulkarni, doc_joshi, doc_sharma]:
            for day in [SlotTemplate.Weekday.MONDAY, SlotTemplate.Weekday.WEDNESDAY, SlotTemplate.Weekday.FRIDAY]:
                tmpl, _ = SlotTemplate.objects.get_or_create(
                    hospital=hospital,
                    doctor=doc,
                    weekday=day,
                    defaults={"start_time": datetime.time(9, 0), "end_time": datetime.time(13, 0), "slot_duration_minutes": 15},
                )
                generate_slots(tmpl, weeks_ahead=2)

        # 7. Patients & Timeline / Vault
        p1, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823012345",
            defaults={
                "first_name": "Asha",
                "last_name": "Patil",
                "gender": "female",
                "email": "asha.patil@example.com",
                "city": "Pune",
                "preferred_language": "mr",
                "insurance_provider": "Star Health Insurance",
            },
        )
        p2, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823023456",
            defaults={
                "first_name": "Ramesh",
                "last_name": "Deshmukh",
                "gender": "male",
                "email": "ramesh.d@example.com",
                "city": "Mumbai",
                "preferred_language": "hi",
                "insurance_provider": "HDFC ERGO Health",
            },
        )
        p3, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823034567",
            defaults={
                "first_name": "Sunita",
                "last_name": "Pawar",
                "gender": "female",
                "city": "Satara",
                "preferred_language": "mr",
            },
        )
        p4, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823045678",
            defaults={
                "first_name": "Rahul",
                "last_name": "Shinde",
                "gender": "male",
                "city": "Pimpri-Chinchwad",
                "preferred_language": "en",
            },
        )

        TimelineEvent.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            summary="Registered as new OPD patient via Call Screen-pop",
            defaults={"event_type": "note", "occurred_at": timezone.now() - datetime.timedelta(days=2)},
        )

        Document.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            title="Blood Test Report - Complete Blood Count",
            defaults={"category": "report", "notes": "Hemoglobin 13.2 g/dL, Normal WBC count."},
        )

        # 8. Enquiries (Lead Pipeline)
        Enquiry.objects.get_or_create(
            hospital=hospital,
            mobile="9823012345",
            defaults={
                "patient": p1,
                "name": "Asha Patil",
                "source": "ivr",
                "department": opd,
                "service_requested": "General Medical Checkup",
                "urgency": "normal",
                "stage": "scheduled",
                "assigned_to": frontdesk_user,
            },
        )
        Enquiry.objects.get_or_create(
            hospital=hospital,
            mobile="9823056789",
            defaults={
                "name": "Sanjay Jagtap",
                "source": "whatsapp",
                "department": cardiology,
                "service_requested": "ECG & Cardiology Consultation",
                "urgency": "high",
                "stage": "new",
                "notes": "Patient inquiring about chest tightness on WhatsApp.",
            },
        )

        # 9. Telephony & Callback Tasks (RNR Queue)
        call_answered, _ = Call.objects.get_or_create(
            hospital=hospital,
            from_number="9823012345",
            defaults={
                "direction": "inbound",
                "status": "answered",
                "to_number": "020-27123456",
                "patient": p1,
                "department": opd,
                "operator": operator_user,
                "started_at": timezone.now() - datetime.timedelta(hours=5),
                "duration_seconds": 142,
                "call_reason": "OPD Appointment Inquiry",
            },
        )

        call_missed, _ = Call.objects.get_or_create(
            hospital=hospital,
            from_number="9890998877",
            defaults={
                "direction": "inbound",
                "status": "missed",
                "to_number": "020-27123456",
                "started_at": timezone.now() - datetime.timedelta(hours=1),
                "duration_seconds": 0,
            },
        )

        CallbackTask.objects.get_or_create(
            hospital=hospital,
            phone_number="9890998877",
            defaults={
                "call": call_missed,
                "department": opd,
                "owner": operator_user,
                "status": "pending",
                "sla_due_at": timezone.now() + datetime.timedelta(minutes=30),
                "notes": "Unanswered inbound call from main IVR.",
            },
        )

        # 10. OPD Appointments
        available_slot = Slot.objects.filter(hospital=hospital, is_blocked=False, appointment__isnull=True).first()
        if available_slot:
            import uuid
            Appointment.objects.get_or_create(
                hospital=hospital,
                slot=available_slot,
                defaults={
                    "patient": p1,
                    "doctor": available_slot.doctor,
                    "status": "checked_in",
                    "source": "crm",
                    "reason": "Routine OPD Consultation",
                    "booked_by": frontdesk_user,
                    "registration_token": uuid.uuid4().hex,
                },
            )


        # 11. Omnichannel Messages (Inbox)
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Hi Asha Patil, your OPD appointment with Dr. Ramesh Kulkarni is confirmed.",
            defaults={
                "channel": "whatsapp",
                "direction": "outbound",
                "status": "delivered",
                "sent_by": frontdesk_user,
            },
        )
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Thank you! Will arrive 10 minutes early.",
            defaults={
                "channel": "whatsapp",
                "direction": "inbound",
                "status": "received",
            },
        )

        # 12. Feedback, NPS & Complaints
        nps_req1, _ = FeedbackRequest.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            defaults={"doctor": doc_kulkarni, "department": opd, "status": "responded", "sent_at": timezone.now() - datetime.timedelta(days=1)},
        )
        NPSResponse.objects.get_or_create(
            hospital=hospital,
            feedback_request=nps_req1,
            defaults={
                "patient": p1,
                "doctor": doc_kulkarni,
                "department": opd,
                "score": 10,
                "category": "promoter",
                "comment": "Excellent experience with Dr. Kulkarni and front desk staff!",
            },
        )

        # Detractor response & service recovery
        nps_req2, _ = FeedbackRequest.objects.get_or_create(
            hospital=hospital,
            patient=p3,
            defaults={"doctor": doc_sharma, "department": ortho, "status": "responded", "sent_at": timezone.now() - datetime.timedelta(hours=12)},
        )
        nps_detractor, _ = NPSResponse.objects.get_or_create(
            hospital=hospital,
            feedback_request=nps_req2,
            defaults={
                "patient": p3,
                "doctor": doc_sharma,
                "department": ortho,
                "score": 4,
                "category": "detractor",
                "comment": "Long waiting time at the reception before OPD consultation.",
            },
        )

        ServiceRecoveryTask.objects.get_or_create(
            hospital=hospital,
            nps_response=nps_detractor,
            defaults={
                "owner": frontdesk_user,
                "status": "pending",
                "sla_due_at": timezone.now() + datetime.timedelta(hours=24),
                "resolution_notes": "",
            },
        )

        Complaint.objects.get_or_create(
            hospital=hospital,
            patient=p3,
            defaults={
                "department": opd,
                "description": "Waiting time exceeded 45 minutes past scheduled slot time.",
                "root_cause": "Emergency procedure delayed OPD start time.",
                "status": "investigating",
                "owner": frontdesk_user,
            },
        )

        # 13. Automation Tasks & Escalation Rules
        Task.objects.get_or_create(
            hospital=hospital,
            title="Follow-up call with no-show patient",
            defaults={
                "description": "Call Ramesh Deshmukh to reschedule missed OPD slot",
                "status": "pending",
                "priority": "high",
                "due_at": timezone.now() + datetime.timedelta(hours=4),
                "owner": frontdesk_user,
            },
        )

        EscalationRule.objects.get_or_create(
            hospital=hospital,
            name="Unanswered Call RNR SLA Escalation (15 Mins)",
            defaults={
                "applies_to": "callback_task",
                "department": opd,
                "escalate_after_minutes": 15,
                "is_active": True,
            },
        )

        # 14. Phase 3: Referral Doctor CRM
        from apps.referrals.models import FieldVisit, ReferralRecord, ReferringDoctor
        ref_doc1, _ = ReferringDoctor.objects.get_or_create(
            hospital=hospital,
            name="Dr. Milind Soman",
            defaults={
                "speciality": "General Practitioner",
                "clinic_name": "Soman Clinic, Kothrud",
                "mobile": "9822011223",
                "email": "dr.soman@example.com",
                "city": "Pune",
                "tier": "gold",
                "notes": "Top referring practitioner for Cardiology and General Surgery",
            },
        )
        ref_doc2, _ = ReferringDoctor.objects.get_or_create(
            hospital=hospital,
            name="Dr. Snehal Patil",
            defaults={
                "speciality": "Consultant Physician",
                "clinic_name": "Patil Family Clinic, Baner",
                "mobile": "9822033445",
                "email": "dr.patil@example.com",
                "city": "Pune",
                "tier": "silver",
            },
        )

        ReferralRecord.objects.get_or_create(
            hospital=hospital,
            referring_doctor=ref_doc1,
            patient=p1,
            defaults={
                "department": opd,
                "attributed_revenue": 15000.00,
                "commission_percentage": 10.00,
                "status": "converted",
            },
        )
        FieldVisit.objects.get_or_create(
            hospital=hospital,
            referring_doctor=ref_doc1,
            visit_date=timezone.now().date() - datetime.timedelta(days=3),
            defaults={
                "visited_by": owner_user,
                "notes": "Discussed monthly referral statement and handed over Diwali greeting brochure.",
                "outcome": "Promised 5+ new patient referrals this month",
            },
        )

        # 15. Phase 3: Health Packages & Campaigns
        from apps.packages.models import CampRegistration, Campaign, HealthPackage
        pkg1, _ = HealthPackage.objects.get_or_create(
            hospital=hospital,
            code="PKG-CARD-01",
            defaults={
                "name": "Executive Master Cardiac Package",
                "category": "cardiac",
                "price": 4999.00,
                "description": "Comprehensive cardiac screening including ECG, Lipid Profile, TMT, and Cardiologist Consultation.",
                "included_tests": ["ECG", "2D Echo", "TMT", "Lipid Profile", "HbA1c", "Doctor Consult"],
                "is_active": True,
            },
        )
        pkg2, _ = HealthPackage.objects.get_or_create(
            hospital=hospital,
            code="PKG-FULL-01",
            defaults={
                "name": "Whole Body Wellness Screening",
                "category": "full_body",
                "price": 2999.00,
                "description": "65 Parameter full body health screening.",
                "included_tests": ["CBC", "KFT", "LFT", "Thyroid Profile", "USG Abdomen"],
                "is_active": True,
            },
        )

        camp1, _ = Campaign.objects.get_or_create(
            hospital=hospital,
            name="Pune Senior Citizen Health Camp 2026",
            defaults={
                "campaign_type": "health_camp",
                "budget": 50000.00,
                "actual_spend": 35000.00,
                "status": "active",
                "start_date": timezone.now().date() - datetime.timedelta(days=10),
                "end_date": timezone.now().date() + datetime.timedelta(days=20),
            },
        )
        CampRegistration.objects.get_or_create(
            hospital=hospital,
            campaign=camp1,
            patient_name="Vijay Deshpande",
            defaults={
                "mobile": "9890112233",
                "stage": "opd_converted",
                "revenue_generated": 4999.00,
            },
        )

        # 16. Phase 3: TPA / Pre-Authorization Desk
        from apps.tpa.models import PreAuthRequest, TPACompany
        tpa1, _ = TPACompany.objects.get_or_create(
            hospital=hospital,
            code="TPA-STAR-01",
            defaults={
                "name": "Star Health Insurance TPA",
                "contact_person": "Mr. Amit Shah",
                "phone": "020-25678900",
                "email": "claims@starhealth.example",
                "claim_submission_email": "preauth@starhealth.example",
                "avg_tat_days": 2,
            },
        )
        PreAuthRequest.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            tpa_company=tpa1,
            defaults={
                "policy_number": "POL-STAR-998877",
                "claim_amount": 75000.00,
                "approved_amount": 65000.00,
                "status": "approved",
                "checklist": {"id_proof": True, "doctor_prescription": True, "discharge_summary": True},
                "approved_at": timezone.now() - datetime.timedelta(days=1),
            },
        )

        # 17. Seed Multi-Branch Hospital Chain & SaaS Super Admin Accounts
        saas_owner, _ = User.objects.get_or_create(
            email="saas_owner@hospital-crm.com",
            defaults={
                "first_name": "SaaS Platform",
                "last_name": "Super Admin",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        saas_owner.set_password("changeme123")
        saas_owner.save()

        # Single Hospital Chain Owner managing all 3 Polynexus Branches
        h_baner = hospital
        h_kothrud, _ = Hospital.objects.get_or_create(
            slug="polynexus-kothrud",
            defaults={"name": "Polynexus Hospital (Kothrud Branch)", "city": "Pune", "state": "Maharashtra"},
        )
        h_wakad, _ = Hospital.objects.get_or_create(
            slug="polynexus-wakad",
            defaults={"name": "Polynexus Hospital (Wakad Branch)", "city": "Pune", "state": "Maharashtra"},
        )

        group_owner, _ = User.objects.get_or_create(
            email="group_owner@polynexus.com",
            defaults={
                "first_name": "Dr. Vikram",
                "last_name": "Pol (Group Owner)",
                "hospital": h_baner,
                "role": owner_role,
            },
        )
        group_owner.set_password("changeme123")
        group_owner.save()

        # Print success table
        self.stdout.write(self.style.SUCCESS("\n========================================================"))
        self.stdout.write(self.style.SUCCESS("Successfully Seeded Demo SaaS & Multi-Branch Accounts!"))
        self.stdout.write(self.style.SUCCESS("========================================================\n"))
        self.stdout.write(f"Password for all accounts: {password}\n")
        self.stdout.write("1. SAAS PLATFORM OWNER (Software Vendor):")
        self.stdout.write("   • Email : saas_owner@hospital-crm.com")
        self.stdout.write("   • Role  : SaaS Superuser / Global Platform Owner\n")
        self.stdout.write("2. HOSPITAL CHAIN GROUP OWNER (3 Branches: Baner, Kothrud, Wakad):")
        self.stdout.write("   • Email : group_owner@polynexus.com")
        self.stdout.write("   • Role  : Managing Director / Hospital Chain Owner\n")
        self.stdout.write("3. SINGLE HOSPITAL STAFF ACCOUNTS:")
        self.stdout.write("   • Owner / Admin  : owner@demo-hospital.example")
        self.stdout.write("   • Front Desk     : frontdesk@demo-hospital.example")
        self.stdout.write("   • OPD Doctor     : doctor@demo-hospital.example")
        self.stdout.write("   • Telephony Op.  : operator@demo-hospital.example\n")


