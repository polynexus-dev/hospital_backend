"""Hospital, departments, staff accounts + roles, doctors, patients."""
from datetime import date, time, timedelta

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor
from apps.core.models import Department, Hospital, ALL_MODULE_KEYS
from apps.patients.models import Patient

from .common import AREAS, BLOOD, HOSPITAL_NAME, PASSWORD, RNG, TODAY, chance, person, phone, pick

BLOOD_CODE = {"A+": "a_positive", "A-": "a_negative", "B+": "b_positive", "B-": "b_negative", "AB+": "ab_positive", "AB-": "ab_negative", "O+": "o_positive", "O-": "o_negative"}

DEPARTMENTS = [
    ("General Medicine", "GM"), ("Cardiology", "CARD"), ("Orthopaedics", "ORTHO"), ("Paediatrics", "PAED"), ("Obstetrics & Gynaecology", "OBG"),
    ("General Surgery", "GS"), ("Neurology", "NEURO"), ("ENT", "ENT"), ("Dermatology", "DERM"), ("Ophthalmology", "OPH"), ("Nephrology", "NEPH"),
    ("Pulmonology", "PULM"), ("Emergency Medicine", "EM"), ("Radiology", "RAD"), ("Pathology", "PATH"), ("Anaesthesiology", "ANES"),
]

DOCTORS = [
    ("Anil", "Kulkarni", "male", "General Medicine", "MD (Internal Medicine)"), ("Sunita", "Deshpande", "female", "General Medicine", "MD (General Medicine)"),
    ("Rajesh", "Mehta", "male", "Cardiology", "DM (Cardiology)"), ("Priya", "Iyer", "female", "Cardiology", "DM (Cardiology)"),
    ("Vikram", "Patil", "male", "Orthopaedics", "MS (Orthopaedics)"), ("Sameer", "Joshi", "male", "Orthopaedics", "MS (Ortho), Fellowship Arthroplasty"),
    ("Kavya", "Nair", "female", "Paediatrics", "MD (Paediatrics)"), ("Ashok", "Rao", "male", "Paediatrics", "DNB (Paediatrics)"),
    ("Meera", "Gokhale", "female", "Obstetrics & Gynaecology", "MS (OBG)"), ("Swati", "Apte", "female", "Obstetrics & Gynaecology", "DGO, DNB (OBG)"),
    ("Suresh", "Reddy", "male", "General Surgery", "MS (General Surgery)"), ("Nikhil", "Shah", "male", "General Surgery", "MS, FMAS (Laparoscopy)"),
    ("Aditi", "Phadke", "female", "Neurology", "DM (Neurology)"), ("Harish", "Menon", "male", "ENT", "MS (ENT)"),
    ("Pallavi", "Karve", "female", "Dermatology", "MD (Dermatology)"), ("Mohan", "Bapat", "male", "Ophthalmology", "MS (Ophthalmology)"),
    ("Deepak", "Agarwal", "male", "Nephrology", "DM (Nephrology)"), ("Smita", "Jain", "female", "Pulmonology", "MD (Pulmonary Medicine)"),
    ("Kiran", "Sawant", "male", "Emergency Medicine", "MD (Emergency Medicine)"), ("Shweta", "Kapoor", "female", "Anaesthesiology", "MD (Anaesthesia)"),
    ("Prakash", "Wagh", "male", "Radiology", "MD (Radiodiagnosis)"), ("Madhuri", "Thakur", "female", "Pathology", "MD (Pathology)"),
]

# (first, last, gender, role template, role name, department)
STAFF = [
    ("Neha", "Bhosale", "female", "nurse", "Staff Nurse", "General Medicine"), ("Rutuja", "Kadam", "female", "nurse", "Staff Nurse", "General Medicine"),
    ("Sayali", "Mane", "female", "nurse", "Staff Nurse", "Orthopaedics"), ("Jyoti", "Salunkhe", "female", "nurse", "ICU Nurse", "General Medicine"),
    ("Archana", "Naik", "female", "nurse", "Staff Nurse", "Obstetrics & Gynaecology"), ("Omkar", "Chavan", "male", "nurse", "OT Nurse", "General Surgery"),
    ("Pooja", "Shinde", "female", "receptionist", "Front Office", "General Medicine"), ("Tejas", "More", "male", "receptionist", "Front Office", "General Medicine"),
    ("Rekha", "Jadhav", "female", "billing_executive", "Billing Executive", "General Medicine"),
    ("Ganesh", "Pawar", "male", "lab_technician", "Lab Technician", "Pathology"), ("Lata", "Gaikwad", "female", "lab_technician", "Lab Technician", "Pathology"),
    ("Yash", "Deshmukh", "male", "radiology_technician", "Radiology Technician", "Radiology"),
    ("Manoj", "Kale", "male", "pharmacist", "Pharmacist", "General Medicine"), ("Bhavana", "Mishra", "female", "pharmacist", "Pharmacist", "General Medicine"),
    ("Sanjay", "Pandey", "male", "finance_manager", "Finance Manager", "General Medicine"), ("Ketaki", "Joshi", "female", "hr_manager", "HR Manager", "General Medicine"),
    ("Riya", "Sharma", "female", "crm_manager", "CRM Manager", "General Medicine"),
    ("Kunal", "Verma", "male", "crm_executive", "Patient Relations Executive", "General Medicine"),
    ("Tanvi", "Gupta", "female", "crm_executive", "Patient Relations Executive", "General Medicine"),
    ("Abhishek", "Malhotra", "male", "call_centre_executive", "Call Centre Executive", "General Medicine"),
    ("Isha", "Agarwal", "female", "call_centre_executive", "Call Centre Executive", "General Medicine"),
    ("Pranav", "Iyer", "male", "marketing_manager", "Marketing Manager", "General Medicine"),
    ("Mrunal", "Apte", "female", "him_officer", "Medical Records Officer", "General Medicine"),
    ("Hemant", "Rao", "male", "ot_manager", "OT Manager", "General Surgery"),
]


def setup_hospital():
    h = Hospital.objects.get(slug="demo-hospital")
    h.name = HOSPITAL_NAME
    h.city, h.state = "Pune", "Maharashtra"
    h.address = "Survey No. 42, Baner–Pashan Link Road, Baner, Pune, Maharashtra 411045"
    h.helpline_phone = "1800-200-0001"
    h.enabled_modules = list(ALL_MODULE_KEYS)
    h.grievance_officer_name = "Ms. Gauri Kulkarni"
    h.grievance_officer_email = "grievance@demo.polynexus.in"
    h.save()
    return h


BASE_ROLE_TEMPLATES = {"Hospital Owner / Admin": "owner", "Front Desk Officer": "front_desk", "OPD Doctor": "doctor", "Telephony Operator": "telephony_operator"}


def fix_base_roles(h):
    """The base seed's roles were created without a permission template; give them theirs."""
    from apps.accounts.role_sync import sync_role

    for r in Role.objects.filter(template__in=["", None]):
        template = BASE_ROLE_TEMPLATES.get(r.name, "")
        if template:
            r.template = template
            r.save(update_fields=["template"])
            sync_role(r)


def tidy_base_doctors(h):
    """The base seed stores "Dr." inside doctor names (the app adds the title itself) and gives them no schedule: keep them out of the live calendar."""
    for d in Doctor.objects.filter(hospital=h, name__startswith="Dr. "):
        d.name = d.name[4:]
        d.is_active = False
        d.save(update_fields=["name", "is_active"])


def departments(h):
    out = {}
    for name, code in DEPARTMENTS:
        d, _ = Department.objects.get_or_create(hospital=h, name=name, defaults={"code": code})
        out[name] = d
    return out


def role(h, template, name, dept=None):
    r = Role.objects.filter(hospital=h, name=name).first()
    if r is None:
        r = Role.objects.create(hospital=h, name=name, template=template, department=dept)
    return r


def staff(h, depts):
    users = {}
    for first, last, gender, template, role_name, dept in STAFF:
        email = f"{first}.{last}".lower() + "@demo.polynexus.in"
        u = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email, password=PASSWORD, hospital=h, first_name=first, last_name=last, department=depts[dept])
        assign_role(u, role(h, template, role_name, depts[dept]))
        users.setdefault(template, []).append(u)
    return users


def doctors(h, depts):
    doc_role = role(h, "doctor", "Consultant Doctor")
    surg_role = role(h, "surgeon", "Consultant Surgeon")
    anaes_role = role(h, "anaesthetist", "Consultant Anaesthetist")
    out = []
    for i, (first, last, gender, dept, degree) in enumerate(DOCTORS):
        email = f"dr.{first}.{last}".lower() + "@demo.polynexus.in"
        u = User.objects.filter(email=email).first() or User.objects.create_user(
            email=email, password=PASSWORD, hospital=h, first_name=first, last_name=last, department=depts[dept])
        r = surg_role if dept in ("General Surgery", "Orthopaedics", "Obstetrics & Gynaecology") else anaes_role if dept == "Anaesthesiology" else doc_role
        assign_role(u, r)
        doc = Doctor.objects.filter(hospital=h, user=u).first() or Doctor.objects.create(
            hospital=h, user=u, department=depts[dept], name=f"{first} {last}", speciality=degree,
            phone=phone(), email=email, default_consultation_minutes=15 if dept not in ("Neurology", "Nephrology") else 20)
        out.append(doc)
    return out


def patients(h, n=240):
    out = []
    for i in range(n):
        first, last, gender = person()
        age = RNG.choice([RNG.randint(1, 12), RNG.randint(18, 40), RNG.randint(30, 60), RNG.randint(45, 82)])
        dob = TODAY.replace(year=TODAY.year - age) - timedelta(days=RNG.randint(0, 360))
        area, pin = pick(AREAS)
        insured = chance(0.35)
        p = Patient.objects.create(
            hospital=h, title=("Mr." if gender == "male" else "Ms.") if age >= 18 else ("Master" if gender == "male" else "Miss"),
            first_name=first, last_name=last, gender=gender, date_of_birth=dob, mobile=phone(),
            email=f"{first}.{last}{i}".lower() + "@example.com" if chance(0.6) else "",
            address=f"{RNG.randint(1, 240)}, {pick(['Sai', 'Shree', 'Ganesh', 'Vrindavan', 'Sukhshanti', 'Mantri', 'Kumar'])} {pick(['Residency', 'Heights', 'Park', 'Society', 'Apartments'])}, {area}",
            city="Pune", state="Maharashtra", district="Pune", village_or_area=area, pincode=pin,
            blood_group=BLOOD_CODE[pick(BLOOD)],
            preferred_language=pick(["mr", "mr", "hi", "en"]),
            insurance_provider=pick(["Star Health", "HDFC ERGO", "ICICI Lombard", "Niva Bupa", "Care Health"]) if insured else "",
            insurance_policy_number=f"POL{RNG.randint(10**8, 10**9 - 1)}" if insured else "",
            attendant_name=f"{person()[0]} {last}" if chance(0.5) else "", attendant_relation=pick(["spouse", "son", "daughter", "father", "mother"]) if chance(0.5) else "",
            tags=["senior citizen"] if age >= 60 else [],
        )
        out.append(p)
    return out
