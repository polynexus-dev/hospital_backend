"""Shared fictional data and helpers for the Polynexus documentation demo.

Every person, phone number, policy and medical record here is invented for
product demonstration. Phone numbers use the reserved 90000 xxxxx style
(9000…) so none belongs to a real subscriber pattern we know of."""
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

RNG = random.Random(20261015)
TODAY = timezone.localdate()
NOW = timezone.now()
HOSPITAL_NAME = "Polynexus Demo Multispeciality Hospital"
PASSWORD = "Demo@Polynexus2026"

MALE = ["Aarav", "Vihaan", "Aditya", "Arjun", "Rohan", "Siddharth", "Kunal", "Nikhil", "Rahul", "Amit", "Sanjay", "Vikram", "Prakash", "Suresh",
        "Mahesh", "Ganesh", "Rajesh", "Anil", "Sunil", "Deepak", "Manoj", "Ashok", "Vinay", "Harish", "Omkar", "Tejas", "Yash", "Pranav", "Sameer",
        "Abhishek", "Ravi", "Kiran", "Shrikant", "Dattatray", "Mohan", "Sachin", "Ajay", "Nitin", "Vijay", "Hemant", "Ishaan", "Kabir", "Atharva"]
FEMALE = ["Ananya", "Priya", "Sneha", "Pooja", "Neha", "Kavya", "Aditi", "Shruti", "Meera", "Riya", "Anjali", "Swati", "Sunita", "Asha", "Lata",
          "Rekha", "Vaishali", "Manisha", "Pallavi", "Smita", "Archana", "Jyoti", "Madhuri", "Shweta", "Sayali", "Rutuja", "Gauri", "Isha", "Tanvi",
          "Nandini", "Aishwarya", "Prajakta", "Mrunal", "Ketaki", "Radha", "Sarika", "Usha", "Kalpana", "Bhavana", "Divya", "Saanvi", "Myra"]
SURNAMES = ["Kulkarni", "Deshpande", "Joshi", "Patil", "Pawar", "Jadhav", "Shinde", "More", "Gaikwad", "Chavan", "Kale", "Bhosale", "Deshmukh",
            "Sharma", "Verma", "Gupta", "Mehta", "Shah", "Iyer", "Nair", "Reddy", "Rao", "Menon", "Pillai", "Kapoor", "Malhotra", "Agarwal", "Jain",
            "Sawant", "Thakur", "Kadam", "Salunkhe", "Mane", "Wagh", "Naik", "Gokhale", "Apte", "Bapat", "Karve", "Phadke", "Mishra", "Pandey"]
AREAS = [("Baner", "411045"), ("Aundh", "411007"), ("Kothrud", "411038"), ("Wakad", "411057"), ("Hinjewadi", "411057"), ("Pashan", "411021"),
         ("Shivajinagar", "411005"), ("Viman Nagar", "411014"), ("Kharadi", "411014"), ("Hadapsar", "411028"), ("Pimple Saudagar", "411027"),
         ("Bavdhan", "411021"), ("Deccan Gymkhana", "411004"), ("Karve Nagar", "411052"), ("Sus", "411021"), ("Balewadi", "411045")]
BLOOD = ["A+", "A+", "B+", "B+", "O+", "O+", "O+", "AB+", "A-", "B-", "O-", "AB-"]

_phone_seq = iter(range(1000, 99999))


def phone():
    """Fictional mobile: 90000-prefixed demo series."""
    return f"+919000{next(_phone_seq):06d}"[-13:]


def person(gender=None):
    gender = gender or RNG.choice(["male", "female"])
    first = RNG.choice(MALE if gender == "male" else FEMALE)
    return first, RNG.choice(SURNAMES), gender


def money(lo, hi, step=50):
    return Decimal(RNG.randrange(lo // step, hi // step + 1) * step)


def days_ago(n, hour=None, minute=None):
    d = TODAY - timedelta(days=n)
    h = hour if hour is not None else RNG.randint(9, 18)
    m = minute if minute is not None else RNG.choice([0, 10, 15, 20, 30, 40, 45, 50])
    return timezone.make_aware(datetime.combine(d, time(h, m)))


def at_today(h, m=0):
    return timezone.make_aware(datetime.combine(TODAY, time(h, m)))


def pick(seq, k=None):
    return RNG.choice(seq) if k is None else RNG.sample(seq, k)


def chance(p):
    return RNG.random() < p


def set_created(obj, when, field="created_at"):
    """Backdate an auto timestamp (auto_now_add fields ignore values passed on create)."""
    type(obj).objects.filter(pk=obj.pk).update(**{field: when})


# ---- clinically plausible patient choice -------------------------------------------
_OBSTETRIC = ("matern", "antenatal", "caesar", "lscs", "pregnan", "delivery", "obstetric", "labour")
_FEMALE = _OBSTETRIC + ("hysterect", "hysteroscop", "gynae", "breast", "cervix", "pap smear", "women")
_MALE = ("prostate",)
_PAED = ("paediatric", "child", "bronchiolitis", "vaccination")


def age_of(p):
    return (TODAY - p.date_of_birth).days // 365 if p.date_of_birth else 40


def suits(p, *texts):
    """Whether a patient's gender and age fit a department / service / diagnosis."""
    t, a = " ".join(str(x) for x in texts if x).lower(), age_of(p)
    if any(k in t for k in _PAED):
        return a < 14
    if any(k in t for k in _OBSTETRIC):
        return p.gender == "female" and 20 <= a <= 42
    if any(k in t for k in _FEMALE):
        return p.gender == "female" and a >= 18
    if any(k in t for k in _MALE):
        return p.gender == "male" and a >= 40
    return a >= 14


def pick_for(pts, *texts):
    fit = [p for p in pts if suits(p, *texts)]
    return pick(fit) if fit else pick(pts)


def first_page_patients(h):
    """The patients on the first page of /patients/ exactly as the application
    serves it. Several screens (appointment calendar, inbox, waitlist) resolve
    patient names only from that page, so live 'today' data uses these patients."""
    from apps.accounts.models import User
    from apps.patients.models import Patient
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(User.objects.get(email="owner@demo-hospital.example"))
    ids = [r["id"] for r in client.get("/api/v1/patients/").json()["results"]]
    by_id = Patient.objects.in_bulk(ids)
    return [by_id[i] for i in ids if i in by_id]


def first_page_users(h, roles=("CRM Manager", "Call Centre Executive", "Telephony Operator", "Patient Relations Executive", "Front Office")):
    """Contact-centre users on the first page of /users/ (screens resolve owner names from it)."""
    from apps.accounts.models import User
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(User.objects.get(email="owner@demo-hospital.example"))
    ids = [r["id"] for r in client.get("/api/v1/users/").json()["results"]]
    return list(User.objects.filter(pk__in=ids, role__name__in=roles).order_by("pk"))
