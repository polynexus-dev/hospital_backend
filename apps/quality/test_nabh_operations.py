"""NABH elements in admissions, OT, dietary, infection control, procurement,
supplier payments, HR, pharmacy and shared masters that had code but no test."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.hr.models import Employee
from apps.patients.models import Patient, Prescription
from apps.pharmacy.models import Medicine, MedicineBatch


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kulkarni", mobile="9000000701", date_of_birth=date(1966, 6, 6),
                                  insurance_provider="Star Health", insurance_policy_number="POL-1")


@pytest.fixture
def doctor(hospital):
    return Doctor.objects.create(hospital=hospital, name="Dr Rao")


@pytest.fixture
def bed(hospital):
    ward = Ward.objects.create(hospital=hospital, name="Ward C")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="301")
    return Bed.objects.create(hospital=hospital, room=room, bed_number="C1")


@pytest.fixture
def admission(hospital, patient, doctor, bed):
    from apps.ipd.services import admit_patient

    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed, admission_type="planned",
                         department=None, admission_diagnosis="Cholelithiasis", source_encounter=None)


@pytest.fixture
def nurse(hospital):
    u = User.objects.create_user(email="nurse7@x.example", password="x", hospital=hospital, first_name="Neha")
    return Employee.objects.create(hospital=hospital, user=u, employee_code="N007", designation="Staff Nurse")


def client_with_role(hospital, template, email):
    role = Role.objects.create(hospital=hospital, name=template, template=template)
    u = User.objects.create_user(email=email, password="x", hospital=hospital)
    assign_role(u, role)
    c = APIClient()
    c.force_authenticate(user=u)
    return c


# NABH AAC.5.d (package with its included services; exclusions are not modelled, so the matrix marks this Partial)
def test_admission_under_a_package_with_included_services(auth_client, patient, doctor, bed):
    pkg = auth_client.post("/api/v1/packages/catalog/", {"name": "Lap chole package", "code": "LAPC", "category": "full_body", "price": "65000",
                                                                "included_tests": ["CBC", "LFT", "Ultrasound abdomen"]}, format="json")
    assert pkg.status_code == 201, pkg.json()
    adm = auth_client.post("/api/v1/ipd/admissions/", {"patient": patient.pk, "admitting_doctor": doctor.pk, "bed": bed.pk, "admission_type": "planned",
                                                       "admission_diagnosis": "Cholelithiasis", "package": pkg.json()["id"]}, format="json")
    assert adm.status_code == 201, adm.json()
    assert adm.json()["package"] == pkg.json()["id"]


# NABH AAC.5.f
def test_admission_found_by_uhid_and_filled_from_the_patient_record(auth_client, patient, admission):
    found = auth_client.get(f"/api/v1/patients/?search={patient.uhid}").json()["results"]
    assert [p["id"] for p in found] == [patient.pk]
    assert [p["id"] for p in auth_client.get("/api/v1/patients/?search=09000000701").json()["results"]] == [patient.pk]
    assert (found[0]["insurance_provider"], found[0]["insurance_policy_number"]) == ("Star Health", "POL-1")
    adm = auth_client.get(f"/api/v1/ipd/admissions/{admission.pk}/").json()
    assert adm["patient"] == patient.pk and adm["patient_name"] == patient.full_name


# NABH COP.1.b
def test_opd_visit_summary_shows_condition_medication_and_follow_up(auth_client, hospital, patient, user):
    Prescription.objects.create(hospital=hospital, patient=patient, doctor=user, diagnosis="Hypertension", medications=[{"name": "Amlodipine 5mg", "frequency": "OD"}])
    due = timezone.now() + timedelta(days=30)
    Patient.objects.filter(pk=patient.pk).update(next_recall_due_at=due, recall_reason="BP review")
    s = auth_client.get(f"/api/v1/clinical/patients/{patient.pk}/summary/").json()
    assert s["prescriptions"][0]["medications"][0]["name"] == "Amlodipine 5mg"
    assert s["follow_up"]["reason"] == "BP review" and s["follow_up"]["due"]


# NABH COP.6.f
def test_anaesthesia_record_for_the_operation(auth_client, hospital, patient, doctor, admission, user):
    from apps.ot.models import OTSchedule, SurgeryRequest

    sr = SurgeryRequest.objects.create(hospital=hospital, patient=patient, admission=admission, proposed_procedure="Lap chole")
    start = timezone.now() + timedelta(hours=1)
    sch = OTSchedule.objects.create(hospital=hospital, surgery_request=sr, operation_theatre_room="OT1", surgeon=doctor, scheduled_start=start, scheduled_end=start + timedelta(hours=2))
    res = auth_client.post("/api/v1/ot/anaesthesia-records/", {
        "ot_schedule": sch.pk, "anaesthesia_type": "General (ETT)", "anaesthetist": user.pk, "asa_grade": "II", "airway_device": "ETT 7.5",
        "drugs": [{"drug": "Propofol", "dose": "150 mg", "route": "IV"}], "vitals": [{"time": "10:05", "hr": 78, "spo2": 99}], "estimated_blood_loss_ml": 50,
    }, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["drugs"][0]["drug"] == "Propofol" and res.json()["asa_grade"] == "II"


# NABH COP.7.a
def test_dietary_screening_and_consultation(auth_client, patient, admission):
    res = auth_client.post("/api/v1/dietary/consultations/", {"patient": patient.pk, "admission": admission.pk, "height_cm": "170", "weight_kg": "52",
                                                              "nutritional_risk": "high", "assessment": "Poor intake for 2 weeks", "plan": "High-protein diet, ONS twice daily"}, format="json")
    assert res.status_code == 201, res.json()
    assert Decimal(str(res.json()["bmi"])) == Decimal("18.0")


# NABH COP.8.a
def test_infection_case_recorded_and_tracked(auth_client, patient, admission):
    res = auth_client.post("/api/v1/infection-control/hai/", {"patient": patient.pk, "admission": admission.pk, "infection_type": "ssi", "ward": "Ward C",
                                                              "organism": "MRSA", "is_mdro": True, "isolation_required": True}, format="json")
    assert res.status_code == 201, res.json()
    assert auth_client.patch(f"/api/v1/infection-control/hai/{res.json()['id']}/", {"status": "confirmed"}, format="json").json()["status"] == "confirmed"
    assert [i["infection_type"] for i in auth_client.get("/api/v1/infection-control/hai/?infection_type=ssi").json()["results"]] == ["ssi"]


# NABH FPM.1.a
def test_purchase_order_approval_follows_the_value_rule(auth_client, hospital):
    from apps.inventory.models import Item, ItemCategory, POItem, PurchaseApprovalRule, PurchaseOrder

    PurchaseApprovalRule.objects.create(hospital=hospital, min_amount=0, max_amount=50000, approver_role="purchase_manager")
    cat = ItemCategory.objects.create(hospital=hospital, name="Consumables", code="CON")
    item = Item.objects.create(hospital=hospital, category=cat, name="Gloves", code="GLV")
    po = PurchaseOrder.objects.create(hospital=hospital, po_number="PO-9", vendor_name="MedSupply", status="submitted")
    POItem.objects.create(purchase_order=po, item=item, ordered_quantity=100, unit_cost=10)
    pharmacist = client_with_role(hospital, "pharmacist", "pharm@x.example")
    buyer = client_with_role(hospital, "purchase_manager", "buyer@x.example")
    assert pharmacist.post(f"/api/v1/inventory/purchase-orders/{po.pk}/approve/").status_code == 403
    ok = buyer.post(f"/api/v1/inventory/purchase-orders/{po.pk}/approve/")
    assert ok.status_code == 200, ok.json()
    assert ok.json()["value"] == 1000.0


def _approved_invoice(auth_client, name, credit_days, number, email=""):
    vendor = auth_client.post("/api/v1/finance/vendors/", {"name": name, "credit_days": credit_days, "email": email}, format="json").json()
    inv = auth_client.post("/api/v1/finance/vendor-invoices/", {"vendor": vendor["id"], "invoice_number": number, "taxable_amount": "1000", "gst_amount": "0"}, format="json").json()
    approved = auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/approve/", {"override_reason": "No PO: service invoice"}, format="json")
    assert approved.json()["status"] == "approved", approved.json()
    return inv


# NABH FPM.2.e
def test_supplier_payment_schedule_follows_credit_terms(auth_client):
    _approved_invoice(auth_client, "Quick Pay Ltd", 7, "Q-1")
    _approved_invoice(auth_client, "Slow Pay Ltd", 60, "S-1")
    due = auth_client.get("/api/v1/finance/vendor-invoices/payment_schedule/?days=14").json()
    assert [r["vendor"] for r in due] == ["Quick Pay Ltd"] and due[0]["due_date"] == str(timezone.localdate() + timedelta(days=7))


# NABH FPM.2.g
def test_supplier_told_when_payment_is_released(auth_client):
    inv = _approved_invoice(auth_client, "Mail Me Ltd", 30, "M-1", email="ap@mailme.example")
    auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/pay/", {"amount": "400", "reference": "UTR9"}, format="json")
    assert mail.outbox and mail.outbox[-1].to == ["ap@mailme.example"] and "M-1" in mail.outbox[-1].subject
    assert "UTR9" in mail.outbox[-1].body


# NABH HRM.1.a
def test_staff_master_with_professional_details(auth_client, nurse):
    res = auth_client.post("/api/v1/hr/staff-profiles/", {"employee": nurse.pk, "date_of_birth": "1992-03-03", "phone": "9000000799",
                                                          "qualifications": [{"degree": "B.Sc Nursing", "institute": "BVU", "year": 2014}],
                                                          "council_registration": "MNC-12345", "registration_valid_until": str(timezone.localdate() + timedelta(days=20))}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["employee_code"] == "N007"
    assert [r["council_registration"] for r in auth_client.get("/api/v1/hr/staff-profiles/expiring_registrations/?days=30").json()] == ["MNC-12345"]


# NABH HRM.1.g
def test_attendance_recorded_and_absences_reach_payroll(auth_client, nurse):
    month = timezone.localdate().replace(day=1)
    for d in (1, 2):
        res = auth_client.post("/api/v1/hr/attendance/", {"employee": nurse.pk, "date": str(month.replace(day=d)), "status": "absent"}, format="json")
        assert res.status_code == 201, res.json()
    assert len(auth_client.get(f"/api/v1/hr/attendance/?employee={nurse.pk}").json()["results"]) == 2
    auth_client.post("/api/v1/hr/salary-structures/", {"employee": nurse.pk, "basic": "15000"}, format="json")
    run = auth_client.post("/api/v1/hr/payroll-runs/", {"month": str(month)}, format="json").json()
    slip = auth_client.get(f"/api/v1/hr/payroll-runs/{run['id']}/payslips/").json()[0]
    import calendar

    assert Decimal(slip["paid_days"]) == calendar.monthrange(month.year, month.month)[1] - 2


# NABH HRM.3.b
def test_training_calendar_lists_upcoming_programmes(auth_client, nurse):
    for title, days in (("Fire safety", 3), ("BLS refresher", 10), ("Old CME", -5)):
        auth_client.post("/api/v1/hr/trainings/", {"title": title, "kind": "mandatory", "scheduled_at": (timezone.now() + timedelta(days=days)).isoformat(), "invitees": [nurse.pk]}, format="json")
    titles = [t["title"] for t in auth_client.get("/api/v1/hr/trainings/calendar/").json()]
    assert "Fire safety" in titles and "BLS refresher" in titles and "Old CME" not in titles


# NABH IMS.1.h
def test_drug_codes_travel_in_the_abdm_prescription(auth_client, hospital, patient):
    rx = Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Hypertension",
                                     medications=[{"name": "Amlodipine 5mg", "drug_code": "386864001", "frequency": "OD"}])
    bundle = auth_client.get(f"/api/v1/abdm-fhir/prescription/{rx.pk}/").json()
    req = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "MedicationRequest")
    assert req["medicationCodeableConcept"]["coding"][0] == {"system": "http://snomed.info/sct", "code": "386864001", "display": "Amlodipine 5mg"}


# NABH MOM.2.d
def test_pharmacy_stock_report(auth_client, hospital):
    m = Medicine.objects.create(hospital=hospital, name="Paracetamol 500", reorder_level=50)
    MedicineBatch.objects.create(hospital=hospital, medicine=m, batch_number="P1", expiry_date=timezone.localdate() + timedelta(days=200), quantity_available=40, purchase_price=Decimal("1.50"))
    MedicineBatch.objects.create(hospital=hospital, medicine=m, batch_number="OLD", expiry_date=timezone.localdate() - timedelta(days=1), quantity_available=99, purchase_price=Decimal("1.00"))
    row = next(r for r in auth_client.get("/api/v1/pharmacy/medicines/stock_report/").json() if r["name"] == "Paracetamol 500")
    assert (row["quantity"], row["value"], row["batches"], row["below_reorder"]) == (40, 60.0, 1, True)


# NABH MOM.2.e
def test_prescriber_search_puts_formulary_first(auth_client, hospital):
    Medicine.objects.create(hospital=hospital, name="Amoxicillin brand X", generic_name="amoxicillin", is_formulary=False)
    Medicine.objects.create(hospital=hospital, name="Amoxicillin 500", generic_name="amoxicillin", is_formulary=True)
    rows = auth_client.get("/api/v1/pharmacy/medicines/formulary/?q=amoxi").json()
    assert [(r["name"], r["is_formulary"]) for r in rows] == [("Amoxicillin 500", True), ("Amoxicillin brand X", False)]


# NABH MOM.4.a
def test_emergency_medication_list_kept_per_location(auth_client, hospital):
    from apps.pharmacy.models import StockOutEvent

    adr = Medicine.objects.create(hospital=hospital, name="Adrenaline 1mg", is_emergency=True)
    res = auth_client.post("/api/v1/pharmacy/emergency-stock/", {"location": "ER crash cart 1", "medicine": adr.pk, "par_level": 5, "current_quantity": 5}, format="json")
    assert res.status_code == 201, res.json()
    auth_client.patch(f"/api/v1/pharmacy/emergency-stock/{res.json()['id']}/", {"current_quantity": 0}, format="json")
    assert StockOutEvent.objects.filter(medicine=adr, location="ER crash cart 1", is_emergency_medication=True).exists()


# NABH DOM.1.d
def test_master_data_shared_across_modules(auth_client, hospital, patient, department):
    dept_id = department.pk
    assert dept_id in [d["id"] for d in auth_client.get("/api/v1/departments/").json()["results"]]
    doc = auth_client.post("/api/v1/doctors/", {"name": "Dr Heart", "department": dept_id}, format="json")
    assert doc.status_code == 201, doc.json()
    ref = auth_client.post("/api/v1/clinical/referrals/", {"patient": patient.pk, "to_department": dept_id, "reason": "ECG changes"}, format="json")
    assert ref.status_code == 201 and ref.json()["to_department_name"] == department.name


# NABH FPM.4.b
def test_patient_identified_by_uhid_and_verified_mobile_for_insurance(auth_client, patient, monkeypatch):
    monkeypatch.setattr("apps.patients.registration.secrets.randbelow", lambda n: 135790)
    auth_client.post(f"/api/v1/patients/{patient.pk}/verify-mobile/", {}, format="json")
    assert auth_client.post(f"/api/v1/patients/{patient.pk}/verify-mobile/", {"otp": "135790"}, format="json").json() == {"verified": True}
    found = auth_client.get(f"/api/v1/patients/?search={patient.uhid}").json()["results"][0]
    assert found["mobile_verified_at"] and found["insurance_policy_number"] == "POL-1"
