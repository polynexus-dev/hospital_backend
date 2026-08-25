import datetime
import uuid
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Appointment, Doctor, Slot, SlotTemplate, Waitlist
from apps.appointments.services import generate_slots
from apps.automation.models import EscalationRule, Task, Workflow, WorkflowRun, WorkflowStep
from apps.communications.models import Channel, ConsentOptOut, Message, Template, Thread
from apps.core.models import Department, Hospital
from apps.enquiries.models import Enquiry
from apps.feedback.models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask
from apps.integrations.models import HISBillingRecord, HISVisit
from apps.packages.models import CampRegistration, Campaign, HealthPackage
from apps.patients.models import Document, Patient, Prescription, TimelineEvent, record_timeline_event
from apps.referrals.models import FieldVisit, ReferralRecord, ReferringDoctor
from apps.telephony.models import Call, CallbackTask
from apps.tpa.models import PreAuthRequest, TPACompany
from apps.emergency.models import EDVisit, Triage
from apps.ot.models import AnaesthesiaRecord, ConsumableUsage, ImplantUsage, OperativeNote, OTSchedule, PreOpChecklist, SurgeryRequest
from apps.icu.models import ICUAdmission, ICUDailyProgressNote, VentilatorLog
from apps.bloodbank.models import BloodUnit, CrossMatchRequest, Donor, Transfusion
from apps.billing.models import Bill, BillItem, InsuranceClaim, Payment
from apps.inventory.models import Item, ItemCategory, POItem, PurchaseOrder, StockLevel, StockTransaction
from apps.finance.models import Expense, Ledger, Receivable
from apps.hr.models import Attendance, Employee, LeaveRequest, Shift
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission


class Command(BaseCommand):
    help = "Seeds 100% comprehensive demo data for ALL Hospital CRM modules (Users, Patients, Inbox/Threads, Prescriptions, HIS Visits/Bills, Workflows, Waitlists, Telephony, NPS, TPA, Packages, Referrals)."

    def add_arguments(self, parser):
        parser.add_argument("--admin-password", default="changeme123", help="Password for all seeded demo users.")

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["admin_password"]

        # Check if demo data is already seeded to ensure idempotency
        if Hospital.objects.filter(slug="demo-hospital").exists() and User.objects.filter(email="admin@hms.polynexus.in").exists():
            self.stdout.write(self.style.SUCCESS("[SKIP] Demo data is already seeded in the database."))
            return

        self.stdout.write("Seeding data across all modules...")

        # 1. Hospital Tenant & Branches
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

        h_kothrud, _ = Hospital.objects.get_or_create(
            slug="polynexus-kothrud",
            defaults={"name": "Polynexus Hospital (Kothrud Branch)", "city": "Pune", "state": "Maharashtra"},
        )
        h_wakad, _ = Hospital.objects.get_or_create(
            slug="polynexus-wakad",
            defaults={"name": "Polynexus Hospital (Wakad Branch)", "city": "Pune", "state": "Maharashtra"},
        )

        # 2. Departments
        opd, _ = Department.objects.get_or_create(hospital=hospital, name="OPD", defaults={"code": "OPD"})
        cardiology, _ = Department.objects.get_or_create(hospital=hospital, name="Cardiology", defaults={"code": "CARD"})
        ortho, _ = Department.objects.get_or_create(hospital=hospital, name="Orthopedics", defaults={"code": "ORTHO"})
        diag, _ = Department.objects.get_or_create(hospital=hospital, name="Diagnostics", defaults={"code": "DIAG"})
        ipd, _ = Department.objects.get_or_create(hospital=hospital, name="IPD", defaults={"code": "IPD"})

        # 3. Roles
        from apps.accounts.permission_templates import apply_permission_template

        owner_role, _ = Role.objects.get_or_create(
            hospital=hospital,
            name="Hospital Owner / Admin",
            defaults={"template": Role.Template.HOSPITAL_ADMINISTRATOR},
        )
        owner_role.template = Role.Template.HOSPITAL_ADMINISTRATOR
        owner_role.save()
        apply_permission_template(owner_role.group, Role.Template.HOSPITAL_ADMINISTRATOR)

        front_desk_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="Front Desk Officer", defaults={"template": Role.Template.RECEPTIONIST})
        doctor_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="OPD Doctor", defaults={"template": Role.Template.DOCTOR})
        operator_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="Telephony Operator", defaults={"template": Role.Template.CALL_CENTRE_EXECUTIVE})
        tpa_role, _ = Role.objects.get_or_create(hospital=hospital, department=ipd, name="TPA Desk Manager", defaults={"template": Role.Template.INSURANCE_TPA_EXECUTIVE})
        pro_role, _ = Role.objects.get_or_create(hospital=hospital, department=opd, name="Patient Relationship Officer", defaults={"template": Role.Template.CRM_EXECUTIVE})

        # 4. Demo Users
        def create_or_update_user(email, first_name, last_name, dept=None, role=None, is_staff=False, is_super=False, lang="en"):
            user, created = User.objects.get_or_create(
                email=email,
                defaults={
                    "hospital": hospital,
                    "department": dept,
                    "first_name": first_name,
                    "last_name": last_name,
                    "is_staff": is_staff,
                    "is_superuser": is_super,
                    "preferred_language": lang,
                },
            )
            user.is_staff = is_staff
            user.is_superuser = is_super
            if created or not user.check_password(password):
                user.set_password(password)
            user.save()
            if role:
                assign_role(user, role)
            return user

        saas_owner = create_or_update_user("saas_owner@hospital-crm.com", "SaaS Platform", "Super Admin", is_staff=True, is_super=True)
        group_owner = create_or_update_user("group_owner@polynexus.com", "Dr. Vikram", "Pol (Group Owner)", role=owner_role, is_staff=True, is_super=True)
        owner_user = create_or_update_user("owner@demo-hospital.example", "Vikram", "Patil (Owner)", role=owner_role, is_staff=True, is_super=True, lang="mr")
        admin_user = create_or_update_user("admin@hms.polynexus.in", "System", "Admin", role=owner_role, is_staff=True, is_super=True)
        frontdesk_user = create_or_update_user("frontdesk@demo-hospital.example", "Priya", "Sharma (Reception)", dept=opd, role=front_desk_role, lang="mr")
        doctor_user = create_or_update_user("doctor@demo-hospital.example", "Dr. Ramesh", "Kulkarni", dept=opd, role=doctor_role, lang="mr")
        operator_user = create_or_update_user("operator@demo-hospital.example", "Amit", "Deshmukh (Call Center)", dept=opd, role=operator_role, lang="hi")

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

        # 7. Patients & Profiles
        p1, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823012345",
            defaults={
                "first_name": "Asha",
                "last_name": "Patil",
                "gender": "female",
                "email": "asha.patil@example.com",
                "city": "Pune",
                "address": "Flat 402, Shivajinagar, Pune",
                "preferred_language": "mr",
                "insurance_provider": "Star Health Insurance",
                "insurance_policy_number": "POL-STAR-998877",
                "national_id_type": "Aadhaar",
                "national_id_number": "1234-5678-9012",
                "attendant_name": "Suresh Patil",
                "attendant_phone": "9823011111",
                "attendant_relation": "husband",
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
                "address": "B-12, Dadar West, Mumbai",
                "preferred_language": "hi",
                "insurance_provider": "HDFC ERGO Health",
                "insurance_policy_number": "POL-HDFC-554433",
                "national_id_type": "PAN",
                "national_id_number": "ABCDE1234F",
            },
        )

        p3, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823034567",
            defaults={
                "first_name": "Sunita",
                "last_name": "Pawar",
                "gender": "female",
                "email": "sunita.pawar@example.com",
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
                "email": "rahul.shinde@example.com",
                "city": "Pimpri-Chinchwad",
                "preferred_language": "en",
            },
        )

        p5, _ = Patient.objects.get_or_create(
            hospital=hospital,
            mobile="9823056789",
            defaults={
                "first_name": "Sanjay",
                "last_name": "Jagtap",
                "gender": "male",
                "email": "sanjay.jagtap@example.com",
                "city": "Pune",
                "preferred_language": "mr",
            },
        )

        # 8. Patient Document Vault & e-Prescriptions (Phase 1/2)
        doc1, _ = Document.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            title="Blood Test Report - Complete Blood Count (CBC)",
            defaults={"category": "report", "notes": "Hemoglobin 13.2 g/dL, Normal WBC & Platelets.", "uploaded_by": frontdesk_user},
        )

        doc2, _ = Document.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            title="Aadhaar ID Card Scan",
            defaults={"category": "id_proof", "notes": "Verified at front desk check-in.", "uploaded_by": frontdesk_user},
        )

        doc3, _ = Document.objects.get_or_create(
            hospital=hospital,
            patient=p2,
            title="ECG Graph Report & Lipid Profile",
            defaults={"category": "report", "notes": "Mild T-wave inversion in V4-V6. Elevated LDL cholesterol.", "uploaded_by": frontdesk_user},
        )

        # Prescriptions
        rx1, _ = Prescription.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            diagnosis="Hypertension & Mild Upper Respiratory Infection",
            defaults={
                "doctor": doctor_user,
                "symptoms": "Headache, mild dry cough, blood pressure 140/90 mmHg",
                "medications": [
                    {"name": "Tab Telmisartan 40mg", "dosage": "1-0-0", "duration": "30 Days"},
                    {"name": "Tab Paracetamol 650mg", "dosage": "1-0-1", "duration": "5 Days"},
                    {"name": "Syp Benadryl 10ml", "dosage": "0-0-1", "duration": "5 Days"},
                ],
                "lab_orders": ["Repeat BP after 14 days", "Serum Creatinine"],
                "notes": "Reduce salt intake in daily diet. Walk 30 mins daily.",
            },
        )

        rx2, _ = Prescription.objects.get_or_create(
            hospital=hospital,
            patient=p2,
            diagnosis="Suspected Coronary Artery Disease / Angina",
            defaults={
                "doctor": doctor_user,
                "symptoms": "Exertional chest discomfort, dyspnea on walking 500m",
                "medications": [
                    {"name": "Tab Aspirin 75mg", "dosage": "0-1-0", "duration": "30 Days"},
                    {"name": "Tab Atorvastatin 20mg", "dosage": "0-0-1", "duration": "30 Days"},
                    {"name": "Tab Metoprolol 25mg", "dosage": "1-0-0", "duration": "30 Days"},
                ],
                "lab_orders": ["2D Echo", "TMT (Treadmill Test)", "Lipid Profile"],
                "notes": "Referred to Dr. Anjali Joshi for Echo & TMT evaluation.",
            },
        )

        rx3, _ = Prescription.objects.get_or_create(
            hospital=hospital,
            patient=p3,
            diagnosis="Bilateral Osteoarthritis Knee (Grade II)",
            defaults={
                "doctor": doctor_user,
                "symptoms": "Bilateral knee joint pain, morning stiffness for 20 mins",
                "medications": [
                    {"name": "Tab Aceclofenac 100mg + Paracetamol", "dosage": "1-0-1", "duration": "7 Days"},
                    {"name": "Cap Diacerein 50mg", "dosage": "0-0-1", "duration": "30 Days"},
                    {"name": "Sachet Collagen Peptides", "dosage": "1-0-0", "duration": "30 Days"},
                ],
                "lab_orders": ["X-Ray Both Knees AP & Lateral Standing"],
                "notes": "Physiotherapy exercises for quadriceps strengthening advised.",
            },
        )

        # Timeline events
        record_timeline_event(patient=p1, event_type="note", summary="Registered patient via Call Screen-Pop", occurred_at=timezone.now() - datetime.timedelta(days=3), created_by=operator_user)
        record_timeline_event(patient=p1, event_type="document", summary="Uploaded Diagnostic Report: Blood Test CBC", occurred_at=timezone.now() - datetime.timedelta(days=2), source=doc1, created_by=frontdesk_user)
        record_timeline_event(patient=p1, event_type="appointment", summary="Completed OPD Consultation with Dr. Ramesh Kulkarni", occurred_at=timezone.now() - datetime.timedelta(days=1), created_by=doctor_user)

        # 9. Appointments & Waitlists
        slots = list(Slot.objects.filter(hospital=hospital, is_blocked=False, appointment__isnull=True)[:10])
        if len(slots) >= 4:
            # Appt 1: Checked In
            Appointment.objects.get_or_create(
                hospital=hospital,
                slot=slots[0],
                defaults={
                    "patient": p1,
                    "doctor": slots[0].doctor,
                    "status": "checked_in",
                    "source": "crm",
                    "reason": "Follow-up BP Check & Prescription Renewal",
                    "booked_by": frontdesk_user,
                    "registration_token": uuid.uuid4().hex,
                    "checked_in_at": timezone.now() - datetime.timedelta(minutes=20),
                },
            )

            # Appt 2: Confirmed
            Appointment.objects.get_or_create(
                hospital=hospital,
                slot=slots[1],
                defaults={
                    "patient": p2,
                    "doctor": doc_joshi,
                    "status": "confirmed",
                    "source": "whatsapp",
                    "reason": "Cardiology Echo & TMT Evaluation",
                    "booked_by": frontdesk_user,
                    "registration_token": uuid.uuid4().hex,
                },
            )

            # Appt 3: Completed
            Appointment.objects.get_or_create(
                hospital=hospital,
                slot=slots[2],
                defaults={
                    "patient": p3,
                    "doctor": doc_sharma,
                    "status": "completed",
                    "source": "crm",
                    "reason": "Knee Joint Pain Consultation",
                    "booked_by": frontdesk_user,
                    "registration_token": uuid.uuid4().hex,
                    "completed_at": timezone.now() - datetime.timedelta(hours=3),
                },
            )

            # Appt 4: No Show
            Appointment.objects.get_or_create(
                hospital=hospital,
                slot=slots[3],
                defaults={
                    "patient": p4,
                    "doctor": doc_kulkarni,
                    "status": "no_show",
                    "source": "website",
                    "reason": "General Health Checkup",
                    "booked_by": frontdesk_user,
                    "registration_token": uuid.uuid4().hex,
                    "no_show_at": timezone.now() - datetime.timedelta(hours=2),
                },
            )

        # Waitlist entries
        Waitlist.objects.get_or_create(
            hospital=hospital,
            patient=p3,
            doctor=doc_joshi,
            defaults={
                "department": cardiology,
                "preferred_date": timezone.now().date() + datetime.timedelta(days=2),
                "status": "waiting",
                "notes": "Patient requested urgent morning slot with Dr. Anjali Joshi if cancellation occurs.",
            },
        )
        Waitlist.objects.get_or_create(
            hospital=hospital,
            patient=p4,
            doctor=doc_sharma,
            defaults={
                "department": ortho,
                "preferred_date": timezone.now().date() + datetime.timedelta(days=1),
                "status": "waiting",
                "notes": "Waitlisted for knee pain consult.",
            },
        )

        # 10. Communication Templates, Consent, Inbox Threads & Messages
        # Templates
        tmpl_rem24, _ = Template.objects.get_or_create(
            hospital=hospital,
            purpose="appointment_reminder_24h",
            channel="whatsapp",
            language="mr",
            defaults={
                "name": "OPD Appointment Reminder (24 Hours - Marathi)",
                "subject": "Appointment Confirmation Reminder",
                "body": "नमस्कार {patient_name}, तुमचे {doctor_name} यांच्याशी {date} रोजी सकाळी {time} वाजता appointment बुक आहे. कृपया १० मिनिटे आधी या.",
                "is_active": True,
            },
        )
        tmpl_rem24_en, _ = Template.objects.get_or_create(
            hospital=hospital,
            purpose="appointment_reminder_24h",
            channel="whatsapp",
            language="en",
            defaults={
                "name": "OPD Appointment Reminder (24 Hours - English)",
                "subject": "Appointment Confirmation",
                "body": "Dear {patient_name}, your appointment with {doctor_name} is scheduled on {date} at {time}. Please arrive 10 mins early.",
                "is_active": True,
            },
        )
        tmpl_nps, _ = Template.objects.get_or_create(
            hospital=hospital,
            purpose="feedback_request",
            channel="sms",
            language="en",
            defaults={
                "name": "Post-OPD NPS Survey Link",
                "subject": "Hospital Visit Feedback",
                "body": "Hi {patient_name}, thank you for visiting Polynexus Hospital! Please rate your experience: {feedback_link}",
                "is_active": True,
            },
        )

        # Consent Opt-Out ledger
        ConsentOptOut.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            channel="whatsapp",
            defaults={"purpose": "transactional", "is_opted_out": False, "recorded_by": frontdesk_user},
        )
        ConsentOptOut.objects.get_or_create(
            hospital=hospital,
            patient=p2,
            channel="sms",
            defaults={"purpose": "marketing", "is_opted_out": True, "recorded_by": frontdesk_user},
        )

        # Unified Inbox Threads
        thread_p1_wa, _ = Thread.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            channel="whatsapp",
            defaults={
                "owner": frontdesk_user,
                "status": "open",
                "last_message_at": timezone.now() - datetime.timedelta(minutes=15),
                "last_inbound_at": timezone.now() - datetime.timedelta(minutes=15),
                "sla_due_at": timezone.now() + datetime.timedelta(minutes=45),
            },
        )
        thread_p2_wa, _ = Thread.objects.get_or_create(
            hospital=hospital,
            patient=p2,
            channel="whatsapp",
            defaults={
                "owner": operator_user,
                "status": "open",
                "last_message_at": timezone.now() - datetime.timedelta(hours=2),
                "last_inbound_at": timezone.now() - datetime.timedelta(hours=2),
            },
        )
        thread_p5_wa, _ = Thread.objects.get_or_create(
            hospital=hospital,
            patient=p5,
            channel="whatsapp",
            defaults={
                "owner": frontdesk_user,
                "status": "open",
                "last_message_at": timezone.now() - datetime.timedelta(hours=4),
                "last_inbound_at": timezone.now() - datetime.timedelta(hours=4),
            },
        )
        thread_p3_sms, _ = Thread.objects.get_or_create(
            hospital=hospital,
            patient=p3,
            channel="sms",
            defaults={
                "owner": frontdesk_user,
                "status": "closed",
                "last_message_at": timezone.now() - datetime.timedelta(days=1),
            },
        )

        # Messages in Inbox Threads (Multi-turn conversations)
        # Thread 1: Asha Patil (WhatsApp)
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Hi Asha Patil, your OPD appointment with Dr. Ramesh Kulkarni is confirmed for tomorrow at 10:00 AM.",
            defaults={
                "channel": "whatsapp",
                "direction": "outbound",
                "status": "delivered",
                "template": tmpl_rem24,
                "sent_by": frontdesk_user,
                "provider_message_id": "WAMID-1001",
                "sent_at": timezone.now() - datetime.timedelta(hours=24),
                "delivered_at": timezone.now() - datetime.timedelta(hours=24),
            },
        )
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Thank you! Will arrive 10 minutes early. Do I need to bring my previous blood test reports?",
            defaults={
                "channel": "whatsapp",
                "direction": "inbound",
                "status": "received",
                "is_read": True,
                "provider_message_id": "WAMID-1002",
                "sent_at": timezone.now() - datetime.timedelta(hours=23),
            },
        )
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Yes, please bring your latest CBC blood test report or show it on the app.",
            defaults={
                "channel": "whatsapp",
                "direction": "outbound",
                "status": "read",
                "sent_by": frontdesk_user,
                "provider_message_id": "WAMID-1003",
                "sent_at": timezone.now() - datetime.timedelta(hours=22),
            },
        )
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            body="Got it! I am currently at the reception desk.",
            defaults={
                "channel": "whatsapp",
                "direction": "inbound",
                "status": "received",
                "is_read": False,  # Drives unread counter in Inbox
                "provider_message_id": "WAMID-1004",
                "sent_at": timezone.now() - datetime.timedelta(minutes=15),
            },
        )

        # Thread 2: Sanjay Jagtap (WhatsApp - Lead Enquiry)
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p5,
            body="Hello, what are the charges for ECG and Cardiology consultation with Dr. Anjali Joshi?",
            defaults={
                "channel": "whatsapp",
                "direction": "inbound",
                "status": "received",
                "is_read": False,
                "provider_message_id": "WAMID-2001",
                "sent_at": timezone.now() - datetime.timedelta(hours=4),
            },
        )
        Message.objects.get_or_create(
            hospital=hospital,
            patient=p5,
            body="Hello Sanjay! ECG consultation package is ₹750. Dr. Anjali Joshi is available Mon/Wed/Fri 9am-1pm.",
            defaults={
                "channel": "whatsapp",
                "direction": "outbound",
                "status": "delivered",
                "sent_by": frontdesk_user,
                "provider_message_id": "WAMID-2002",
                "sent_at": timezone.now() - datetime.timedelta(hours=3),
            },
        )

        # 11. Enquiries & Lead Pipeline
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
                "notes": "Inquired via IVR, booked OPD appointment.",
            },
        )
        Enquiry.objects.get_or_create(
            hospital=hospital,
            mobile="9823056789",
            defaults={
                "patient": p5,
                "name": "Sanjay Jagtap",
                "source": "whatsapp",
                "department": cardiology,
                "service_requested": "ECG & Cardiology Consultation",
                "urgency": "high",
                "stage": "new",
                "assigned_to": operator_user,
                "notes": "Patient inquiring about chest tightness on WhatsApp. Priority lead.",
            },
        )
        Enquiry.objects.get_or_create(
            hospital=hospital,
            mobile="9890887766",
            defaults={
                "name": "Mahesh Deshpande",
                "source": "website",
                "department": ortho,
                "service_requested": "Knee Replacement Consultation",
                "urgency": "normal",
                "stage": "contacted",
                "assigned_to": frontdesk_user,
                "notes": "Submitted contact form on hospital landing page.",
            },
        )

        # 12. Telephony & Callback Tasks (RNR Queue)
        call1, _ = Call.objects.get_or_create(
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
                "call_reason": "opd",
                "ivr_path": "Main Menu > OPD > Operator",
            },
        )

        call2, _ = Call.objects.get_or_create(
            hospital=hospital,
            from_number="9890998877",
            defaults={
                "direction": "inbound",
                "status": "missed",
                "to_number": "020-27123456",
                "started_at": timezone.now() - datetime.timedelta(hours=1),
                "duration_seconds": 0,
                "ivr_path": "Main Menu > Ring No Reason",
            },
        )

        CallbackTask.objects.get_or_create(
            hospital=hospital,
            phone_number="9890998877",
            defaults={
                "call": call2,
                "department": opd,
                "owner": operator_user,
                "status": "pending",
                "attempt_count": 1,
                "sla_due_at": timezone.now() + datetime.timedelta(minutes=15),
                "notes": "Unanswered inbound call from main IVR lines during peak hours.",
            },
        )

        # 13. Feedback, NPS Responses, Complaints & Service Recovery
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
                "comment": "Excellent experience with Dr. Ramesh Kulkarni and front desk staff!",
            },
        )

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
                "comment": "Long waiting time at the reception before OPD consultation. Took 45 mins past slot time.",
            },
        )

        srv_task, _ = ServiceRecoveryTask.objects.get_or_create(
            hospital=hospital,
            nps_response=nps_detractor,
            defaults={
                "owner": frontdesk_user,
                "status": "pending",
                "sla_due_at": timezone.now() + datetime.timedelta(hours=24),
                "resolution_notes": "Assigned to Priya Sharma to call patient, offer priority queue pass for next visit & issue apology voucher.",
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

        # 14. Phase 2 Visual Workflow Builder & Automation Engine
        wf1, _ = Workflow.objects.get_or_create(
            hospital=hospital,
            name="Missed Call Auto-WhatsApp Responder Workflow",
            defaults={
                "description": "Triggered when an inbound call is missed. Sends an instant WhatsApp message with self-booking link.",
                "trigger_type": "missed_call",
                "is_active": True,
                "created_by": admin_user,
            },
        )
        WorkflowStep.objects.get_or_create(
            workflow=wf1,
            order=1,
            defaults={
                "step_type": "trigger",
                "action_type": "create_task",
                "title": "When Inbound Call Status == Missed",
                "config": {"event": "call_missed"},
            },
        )
        WorkflowStep.objects.get_or_create(
            workflow=wf1,
            order=2,
            defaults={
                "step_type": "action",
                "action_type": "send_whatsapp",
                "title": "Send Auto-Apology & Booking Link via WhatsApp",
                "config": {"template": "missed_call_auto_reply"},
            },
        )

        wf2, _ = Workflow.objects.get_or_create(
            hospital=hospital,
            name="NPS Detractor Immediate SLA Escalation",
            defaults={
                "description": "Fires when an NPS score <= 6 is recorded. Auto-creates Service Recovery Task with 24h SLA.",
                "trigger_type": "nps_detractor",
                "is_active": True,
                "created_by": admin_user,
            },
        )

        WorkflowRun.objects.get_or_create(
            hospital=hospital,
            workflow=wf1,
            trigger_event="call_missed_event_9890998877",
            defaults={
                "status": "success",
                "context": {"from_number": "9890998877", "duration": 0},
                "log_output": ["Trigger fired: missed call detected", "WhatsApp template 'missed_call_auto_reply' sent", "CallbackTask #1 created in queue"],
            },
        )

        # Automation Tasks & Escalation Rules
        Task.objects.get_or_create(
            hospital=hospital,
            title="Follow-up call with no-show patient (Rahul Shinde)",
            defaults={
                "description": "Call Rahul Shinde to reschedule missed OPD slot with Dr. Ramesh Kulkarni.",
                "status": "pending",
                "priority": "high",
                "due_at": timezone.now() + datetime.timedelta(hours=4),
                "owner": frontdesk_user,
                "department": opd,
            },
        )
        Task.objects.get_or_create(
            hospital=hospital,
            title="Verify TPA Pre-Auth documents for Ramesh Deshmukh",
            defaults={
                "description": "Verify HDFC ERGO insurance card and doctor prescription before submitting claim.",
                "status": "in_progress",
                "priority": "normal",
                "due_at": timezone.now() + datetime.timedelta(hours=12),
                "owner": frontdesk_user,
                "department": ipd,
            },
        )

        EscalationRule.objects.get_or_create(
            hospital=hospital,
            name="Unanswered Call RNR SLA Escalation (15 Mins)",
            defaults={
                "applies_to": "callback_task",
                "department": opd,
                "escalate_after_minutes": 15,
                "escalate_to": owner_user,
                "is_active": True,
            },
        )

        # 15. HIS Integrations (Visit & Billing Cache)
        HISVisit.objects.get_or_create(
            hospital=hospital,
            external_visit_id="HIS-VISIT-1001",
            defaults={
                "patient": p1,
                "visit_type": "opd",
                "visit_date": timezone.now() - datetime.timedelta(days=1),
                "department_name": "General Medicine",
                "doctor_name": "Dr. Ramesh Kulkarni",
                "raw_payload": {"his_code": "GEN-01", "vitals": {"bp": "140/90", "pulse": 78}},
            },
        )
        HISVisit.objects.get_or_create(
            hospital=hospital,
            external_visit_id="HIS-VISIT-1002",
            defaults={
                "patient": p2,
                "visit_type": "ipd",
                "visit_date": timezone.now() - datetime.timedelta(days=5),
                "department_name": "Cardiology",
                "doctor_name": "Dr. Anjali Joshi",
                "raw_payload": {"room_no": "ICU-04", "admission_type": "emergency"},
            },
        )

        HISBillingRecord.objects.get_or_create(
            hospital=hospital,
            external_bill_id="HIS-BILL-5001",
            defaults={
                "patient": p1,
                "bill_date": timezone.now().date() - datetime.timedelta(days=1),
                "total_amount": 500.00,
                "paid_amount": 500.00,
                "status": "paid",
                "raw_payload": {"payment_mode": "UPI / PhonePe"},
            },
        )
        HISBillingRecord.objects.get_or_create(
            hospital=hospital,
            external_bill_id="HIS-BILL-5002",
            defaults={
                "patient": p2,
                "bill_date": timezone.now().date() - datetime.timedelta(days=5),
                "total_amount": 45000.00,
                "paid_amount": 20000.00,
                "status": "partial",
                "raw_payload": {"tpa_claim_pending": True, "cash_deposit": 20000.00},
            },
        )

        # 16. Referral Doctor CRM
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
                "notes": "Top referring practitioner for Cardiology and General Surgery.",
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
                "outcome": "Promised 5+ new patient referrals this month.",
            },
        )

        # 17. Health Packages & Campaigns
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

        # 18. TPA / Pre-Authorization Desk
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
        tpa2, _ = TPACompany.objects.get_or_create(
            hospital=hospital,
            code="TPA-HDFC-01",
            defaults={
                "name": "HDFC ERGO Health TPA Desk",
                "contact_person": "Ms. Ritu Roy",
                "phone": "020-25678911",
                "email": "claims@hdfcergo.example",
                "claim_submission_email": "preauth@hdfcergo.example",
                "avg_tat_days": 1,
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
        # 19. Emergency, OT, ICU, Blood Bank, Billing, Inventory, Finance, HR Demo Data
        ed_visit, _ = EDVisit.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            defaults={"chief_complaint": "Severe Acute Chest Pain & Shortness of Breath", "status": EDVisit.Status.TRIAGED},
        )
        Triage.objects.get_or_create(
            hospital=hospital,
            ed_visit=ed_visit,
            defaults={
                "triage_category": Triage.Category.RESUSCITATION,
                "vitals_summary": "BP 85/55 mmHg, HR 115 bpm, SpO2 86%, Temp 98.6F",
                "triaged_by": doctor_user,
            },
        )

        surg_req, _ = SurgeryRequest.objects.get_or_create(
            hospital=hospital,
            patient=p2,
            defaults={"proposed_procedure": "Right Total Knee Replacement (TKR)", "status": SurgeryRequest.Status.SCHEDULED},
        )
        PreOpChecklist.objects.get_or_create(
            hospital=hospital,
            surgery_request=surg_req,
            defaults={"consent_obtained": True, "fasting_confirmed": True, "site_marked": True},
        )
        now_dt = timezone.now()
        ot_sched, _ = OTSchedule.objects.get_or_create(
            hospital=hospital,
            surgery_request=surg_req,
            defaults={
                "operation_theatre_room": "OT Suite 1",
                "surgeon": doc_sharma,
                "scheduled_start": now_dt,
                "scheduled_end": now_dt + datetime.timedelta(hours=2),
            },
        )
        op_note, _ = OperativeNote.objects.get_or_create(
            hospital=hospital,
            ot_schedule=ot_sched,
            defaults={
                "procedure_performed": "Right Total Knee Arthroplasty with cemented implant",
                "findings": "Grade 4 Kellgren-Lawrence Osteoarthritis with bone-on-bone friction",
                "surgeon": doc_sharma,
                "started_at": now_dt,
                "ended_at": now_dt + datetime.timedelta(hours=2),
            },
        )
        AnaesthesiaRecord.objects.get_or_create(
            hospital=hospital,
            ot_schedule=ot_sched,
            defaults={
                "anaesthesia_type": "Combined Spinal Epidural (CSE)",
                "intra_op_notes": "Hemodynamics stable. 500ml Ringer Lactate infused.",
                "anaesthetist": doctor_user,
            },
        )
        ConsumableUsage.objects.get_or_create(hospital=hospital, ot_schedule=ot_sched, item_name="Vicryl 2-0 Sutures", defaults={"quantity": 4})
        ImplantUsage.objects.get_or_create(
            hospital=hospital,
            ot_schedule=ot_sched,
            implant_name="Titanium Knee Joint Prosthesis",
            defaults={"serial_number": "SN-TKR-2026-8812", "quantity": 1},
        )

        ward, _ = Ward.objects.get_or_create(hospital=hospital, name="ICU Ward")
        room, _ = Room.objects.get_or_create(hospital=hospital, ward=ward, room_number="ICU-101")
        bed, _ = Bed.objects.get_or_create(hospital=hospital, room=room, bed_number="ICU-BED-01")
        admission, _ = Admission.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            defaults={
                "admitting_doctor": doc_kulkarni,
                "bed": bed,
                "admission_type": Admission.AdmissionType.EMERGENCY,
                "status": Admission.Status.ADMITTED,
                "admission_diagnosis": "Acute Myocardial Infarction / Post-Cardiac Arrest",
            },
        )
        icu_adm, _ = ICUAdmission.objects.get_or_create(
            hospital=hospital,
            admission=admission,
            defaults={"bed": bed, "ventilator_required": True},
        )
        VentilatorLog.objects.get_or_create(
            hospital=hospital,
            icu_admission=icu_adm,
            defaults={"mode": "AC/VC", "ventilator_settings": {"PEEP": 8, "FiO2": 50, "TV": 450}},
        )
        ICUDailyProgressNote.objects.get_or_create(
            hospital=hospital,
            icu_admission=icu_adm,
            defaults={"doctor": doc_kulkarni, "note": "Patient stable on low dose vasopressors. ABG normal."},
        )

        donor, _ = Donor.objects.get_or_create(
            hospital=hospital,
            name="Rajesh Kumar (Voluntary)",
            defaults={"blood_group": "O+", "phone": "+919876543210"},
        )
        today_date = timezone.localdate()
        b_unit, _ = BloodUnit.objects.get_or_create(
            hospital=hospital,
            donor=donor,
            blood_group="O+",
            component=BloodUnit.Component.PRBC,
            defaults={
                "collection_date": today_date - datetime.timedelta(days=5),
                "expiry_date": today_date + datetime.timedelta(days=30),
                "status": BloodUnit.Status.AVAILABLE,
            },
        )
        CrossMatchRequest.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            blood_group_required="O+",
            component=BloodUnit.Component.PRBC,
            defaults={"status": CrossMatchRequest.Status.MATCHED, "requested_by": doctor_user},
        )
        Transfusion.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            blood_unit=b_unit,
            defaults={"admission": admission, "issued_by": doctor_user, "reaction_notes": "Uneventful transfusion. Vitals normal."},
        )

        bill, _ = Bill.objects.get_or_create(
            hospital=hospital,
            patient=p1,
            defaults={
                "admission": admission,
                "total_amount": 25000.00,
                "discount_amount": 2000.00,
                "net_amount": 23000.00,
                "status": Bill.Status.PARTIALLY_PAID,
            },
        )
        BillItem.objects.get_or_create(bill=bill, description="ICU Bed Charge (2 Days)", defaults={"quantity": 2, "unit_price": 5000.00, "total_price": 10000.00})
        BillItem.objects.get_or_create(bill=bill, description="Ventilator Monitoring", defaults={"quantity": 2, "unit_price": 4000.00, "total_price": 8000.00})
        BillItem.objects.get_or_create(bill=bill, description="Emergency Triage & Consultation", defaults={"quantity": 1, "unit_price": 7000.00, "total_price": 7000.00})
        Payment.objects.get_or_create(
            hospital=hospital,
            bill=bill,
            defaults={"amount": 10000.00, "payment_method": Payment.PaymentMethod.UPI, "transaction_id": "UPI/9988221100"},
        )
        InsuranceClaim.objects.get_or_create(
            hospital=hospital,
            bill=bill,
            defaults={
                "insurance_company": "Star Health Insurance",
                "policy_number": "POL-STAR-998877",
                "claimed_amount": 23000.00,
                "approved_amount": 20000.00,
                "status": InsuranceClaim.Status.APPROVED,
            },
        )

        cat_surg, _ = ItemCategory.objects.get_or_create(hospital=hospital, name="Surgical Consumables", defaults={"code": "SURG"})
        item_mask, _ = Item.objects.get_or_create(
            hospital=hospital,
            code="MSK-N95",
            defaults={"category": cat_surg, "name": "N95 Surgical Mask Respirator", "unit_of_measure": "pcs", "min_stock_level": 50},
        )
        StockLevel.objects.get_or_create(
            hospital=hospital,
            item=item_mask,
            batch_number="BAT-2026-N95",
            defaults={"quantity_on_hand": 500, "unit_cost": 30.00, "expiry_date": today_date + datetime.timedelta(days=365)},
        )
        po, _ = PurchaseOrder.objects.get_or_create(
            hospital=hospital,
            po_number="PO-2026-0801",
            defaults={"vendor_name": "MedTech Supplies Corp", "status": PurchaseOrder.Status.RECEIVED},
        )
        POItem.objects.get_or_create(purchase_order=po, item=item_mask, defaults={"ordered_quantity": 1000, "received_quantity": 1000, "unit_cost": 25.00})
        StockTransaction.objects.get_or_create(
            hospital=hospital,
            item=item_mask,
            transaction_type=StockTransaction.TransactionType.RECEIPT,
            defaults={"quantity": 1000, "reference": "PO-2026-0801 Receipt"},
        )

        exp, _ = Expense.objects.get_or_create(
            hospital=hospital,
            category="Surgical Supplies Procurement",
            defaults={"amount": 25000.00, "paid_to": "MedTech Supplies Corp", "paid_by": owner_user, "approved_by": owner_user},
        )
        Ledger.objects.get_or_create(
            hospital=hospital,
            reference_type="Expense",
            reference_id=str(exp.id),
            defaults={"entry_type": Ledger.EntryType.EXPENSE, "category": exp.category, "amount": exp.amount},
        )
        Receivable.objects.get_or_create(
            hospital=hospital,
            source_type=Receivable.SourceType.INSURANCE_CLAIM,
            source_id=str(bill.id),
            defaults={"amount": 20000.00, "due_date": today_date + datetime.timedelta(days=15), "status": Receivable.Status.PENDING},
        )

        emp_doc, _ = Employee.objects.get_or_create(
            hospital=hospital,
            user=doctor_user,
            defaults={"employee_code": "EMP-DR-001", "department": opd, "designation": "Senior Consultant Physician", "employment_type": Employee.EmploymentType.PERMANENT},
        )
        Attendance.objects.get_or_create(
            hospital=hospital,
            employee=emp_doc,
            date=today_date,
            defaults={"status": Attendance.Status.PRESENT},
        )
        LeaveRequest.objects.get_or_create(
            hospital=hospital,
            employee=emp_doc,
            leave_type="Casual Leave",
            start_date=today_date + datetime.timedelta(days=7),
            end_date=today_date + datetime.timedelta(days=8),
            defaults={"status": LeaveRequest.Status.APPROVED, "approved_by": owner_user},
        )
        # 20. Seed Multi-Branch Demo Data for Kothrud & Wakad Branches
        pk1, _ = Patient.objects.get_or_create(
            hospital=h_kothrud,
            first_name="Vikram",
            last_name="Joshi",
            mobile="+919811223344",
            defaults={"gender": "M", "city": "Pune"},
        )
        pk2, _ = Patient.objects.get_or_create(
            hospital=h_kothrud,
            first_name="Sunita",
            last_name="Kulkarni",
            mobile="+919822334455",
            defaults={"gender": "F", "city": "Pune"},
        )
        Bill.objects.get_or_create(
            hospital=h_kothrud,
            patient=pk1,
            defaults={"total_amount": 15000.00, "discount_amount": 1000.00, "net_amount": 14000.00, "status": Bill.Status.PAID},
        )

        pw1, _ = Patient.objects.get_or_create(
            hospital=h_wakad,
            first_name="Anand",
            last_name="Shinde",
            mobile="+919833445566",
            defaults={"gender": "M", "city": "Wakad"},
        )
        Bill.objects.get_or_create(
            hospital=h_wakad,
            patient=pw1,
            defaults={"total_amount": 32000.00, "discount_amount": 2000.00, "net_amount": 30000.00, "status": Bill.Status.PARTIALLY_PAID},
        )

        # Print success table
        self.stdout.write(self.style.SUCCESS("\n========================================================"))
        self.stdout.write(self.style.SUCCESS("Successfully Seeded 100% Comprehensive Data in ALL Modules!"))
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
