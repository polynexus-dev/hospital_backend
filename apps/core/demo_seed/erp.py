"""HMS operations: TPA & insurance, finance, inventory & procurement, HR &
payroll, OT, emergency, ICU, blood bank, quality, infection control and
support services."""
import calendar
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.bloodbank.models import BloodUnit, CrossMatchRequest, Donor
from apps.emergency.models import EDVisit, Triage
from apps.facilities.models import Bed
from apps.finance.models import Expense, Ledger, OTTariff, SurgeryGrade, Vendor, VendorInvoice
from apps.hr.models import Attendance, Employee, LeaveRequest, LeaveType, OvertimeEntry, SalaryStructure, Shift
from apps.icu.models import ICUAdmission
from apps.infection_control.models import HAIIncident
from apps.inventory.models import (GoodsReceiptNote, Item, ItemCategory, POItem, PurchaseOrder, RateContract, RateContractLine, StockLevel, Store, StoreIndent)
from apps.ipd.models import Admission
from apps.ot.models import AnaesthesiaRecord, OTSchedule, PreOpChecklist, SurgeryRequest
from apps.quality.models import SafetyIncident
from apps.support_services.models import Ambulance, AmbulanceTrip, EquipmentAsset, HousekeepingTask
from apps.tpa.models import Claim, ClaimMessage, NHCXProfile, NonPayableItem, NonPayableList, PreAuthRequest, TPACompany

from .common import NOW, RNG, TODAY, at_today, chance, days_ago, person, phone, pick, pick_for, suits

INSURERS = [("Star Health & Allied Insurance", "STAR", 2), ("HDFC ERGO General Insurance", "HDFC", 2), ("ICICI Lombard General Insurance", "ICICI", 3),
            ("Niva Bupa Health Insurance", "NIVA", 2), ("Care Health Insurance", "CARE", 3), ("Medi Assist TPA", "MEDI", 2), ("Paramount Health Services TPA", "PARA", 3)]


def tpa(h, pts, docs, users):
    cos = []
    for name, code, tat in INSURERS:
        c, _ = TPACompany.objects.get_or_create(hospital=h, code=code, defaults={"name": name, "avg_tat_days": tat, "contact_person": f"{person()[0]} {person()[1]}",
                                                                              "phone": "+91 22 6000 " + str(RNG.randint(1000, 9999)), "claim_submission_email": f"preauth@{code.lower()}.example.com",
                                                                              "nhcx_participant_code": f"{code.lower()}-payer@hcx.demo"})
        cos.append(c)
    NHCXProfile.objects.get_or_create(hospital=h, defaults={"participant_code": "polynexus-demo-provider@hcx.demo", "hfr_id": "IN2710000000 (demo)", "rohini_id": "8900080000000"})
    kase = [("K80.2", "Calculus of gallbladder", "Laparoscopic cholecystectomy", 65000), ("M17.1", "Primary osteoarthritis, right knee", "Total knee replacement", 245000),
            ("I21.4", "NSTEMI", "Coronary angioplasty with DES", 285000), ("O82", "Caesarean delivery", "LSCS", 75000), ("J18.9", "Pneumonia", "Medical management", 58000),
            ("K35.80", "Acute appendicitis", "Laparoscopic appendicectomy", 72000)]
    pas = []
    for i in range(28):
        icd, dx, proc, amt = pick(kase)
        p = pick_for(pts, dx, proc)
        st = pick(["submitted", "submitted", "query_raised", "approved", "approved", "approved", "rejected"])
        pa = PreAuthRequest.objects.create(hospital=h, patient=p, tpa_company=pick(cos), policy_number=f"POL{RNG.randint(10**8, 10**9 - 1)}", claim_amount=amt,
                                           approved_amount=int(amt * RNG.uniform(0.8, 1.0)) if st == "approved" else 0, status=st, member_id=f"MEM{RNG.randint(100000, 999999)}",
                                           treating_doctor=pick(docs), admission_type=pick(["planned", "emergency"]), expected_admission_date=TODAY + timedelta(days=RNG.randint(-5, 6)),
                                           expected_stay_days=RNG.randint(1, 5), room_category=pick(["general", "semi_private", "private"]), diagnosis_code=icd, diagnosis=dx,
                                           procedure_name=proc, clinical_summary=f"Patient presented with symptoms consistent with {dx.lower()}. Planned for {proc.lower()}.",
                                           cost_breakdown=[{"category": "room", "description": "Room & nursing", "amount": int(amt * 0.2)}, {"category": "procedure", "description": proc, "amount": int(amt * 0.55)},
                                                           {"category": "medicines", "description": "Medicines & consumables", "amount": int(amt * 0.25)}],
                                           checklist={"id_proof": True, "policy_copy": True, "doctor_notes": chance(0.8)})
        PreAuthRequest.objects.filter(pk=pa.pk).update(submitted_at=NOW - timedelta(days=RNG.randint(0, 12), hours=RNG.randint(0, 20)))
        pas.append(pa)
        if st == "query_raised":
            ClaimMessage.objects.create(hospital=h, preauth_request=pa, direction="in", kind="query", subject=f"Query on pre-auth #{pa.pk}: previous discharge summary required",
                                        body="Kindly share the previous discharge summary and investigation reports to process the request.", respond_by=TODAY + timedelta(days=RNG.randint(-1, 2)))
        if st == "approved":
            ClaimMessage.objects.create(hospital=h, preauth_request=pa, direction="in", kind="approval", subject=f"Cashless authorisation – pre-auth #{pa.pk}", amount=pa.approved_amount,
                                        body="Initial approval granted as per policy terms.")
    for i in range(30):
        pa = pick(pas)
        billed = Decimal(int(pa.claim_amount * RNG.uniform(0.85, 1.1)))
        st = pick(["submitted", "under_review", "settled", "settled", "partially_settled", "rejected"])
        c = Claim.objects.create(hospital=h, preauth_request=pa, patient=pa.patient, tpa_company=pa.tpa_company, claim_number=f"CLM{RNG.randint(1000000, 9999999)}", billed_amount=billed,
                                 settled_amount=billed if st == "settled" else int(billed * Decimal("0.82")) if st == "partially_settled" else 0, status=st,
                                 rejection_reason="Pre-existing condition within waiting period." if st == "rejected" else "")
        Claim.objects.filter(pk=c.pk).update(submitted_at=NOW - timedelta(days=RNG.randint(3, 70)))
    # one imported insurer email waiting for review
    ClaimMessage.objects.create(hospital=h, direction="in", kind="approval", subject="Enhancement approved – claim ref attached", body="An enhancement of Rs 25,000 has been approved.",
                                amount=25000, source="email", email_from="cashless@star.example.com", needs_review=True)
    lst, _ = NonPayableList.objects.get_or_create(hospital=h, name="IRDAI list of non-payable items", defaults={"applies_to_categories": ["insurance", "corporate"]})
    for kw, grp in [("gloves", "list_1"), ("mask", "list_1"), ("admission kit", "list_1"), ("diapers", "list_1"), ("thermometer", "list_1"), ("nebuliser kit", "list_3")]:
        NonPayableItem.objects.get_or_create(hospital=h, non_payable_list=lst, keyword=kw, defaults={"group": grp})
    return {"insurers": len(cos), "preauths": len(pas)}


def finance(h, users):
    fm = (users.get("finance_manager") or [None])[0]
    vendors = []
    for name, gst in [("Sahyadri Pharma Distributors", "27AAACS1111A1Z1"), ("Western India Surgicals", "27AAACW2222B1Z2"), ("MedEquip Services Pvt Ltd", "27AAACM3333C1Z3"),
                      ("CleanCare Facility Services", "27AAACC4444D1Z4"), ("Deccan Medical Agencies", "27AAACD5555E1Z5"), ("Oxygen Gases India", "27AAACO6666F1Z6")]:
        v, _ = Vendor.objects.get_or_create(hospital=h, name=name, defaults={"gstin": gst, "state_code": "27", "credit_days": pick([15, 30, 45]), "email": f"accounts@{name.split()[0].lower()}.example.com"})
        vendors.append(v)
    for i in range(24):
        v = pick(vendors)
        taxable = Decimal(RNG.randrange(15000, 380000, 500))
        d = TODAY - timedelta(days=RNG.randint(0, 75))
        VendorInvoice.objects.create(hospital=h, vendor=v, invoice_number=f"{v.name[:3].upper()}/{d:%y%m}/{RNG.randint(100, 999)}", invoice_date=d, taxable_amount=taxable,
                                     gst_amount=(taxable * Decimal("0.12")).quantize(Decimal("1")), total_amount=(taxable * Decimal("1.12")).quantize(Decimal("1")),
                                     due_date=d + timedelta(days=v.credit_days), status=pick(["received", "matched", "approved", "approved", "partially_paid", "paid", "paid", "mismatch"]),
                                     approved_by=fm)
    for cat, lo, hi, to in [("Electricity", 180000, 260000, "MSEDCL"), ("Medical oxygen", 40000, 90000, "Oxygen Gases India"), ("Housekeeping", 120000, 150000, "CleanCare Facility Services"),
                            ("Equipment AMC", 25000, 80000, "MedEquip Services Pvt Ltd"), ("Diesel (DG set)", 15000, 40000, "Indian Oil outlet"), ("Printing & stationery", 6000, 18000, "Shree Printers")]:
        for m in range(3):
            Expense.objects.create(hospital=h, category=cat, amount=Decimal(RNG.randrange(lo, hi, 500)), paid_to=to, paid_by=fm, approved_by=fm, expense_date=TODAY - timedelta(days=30 * m + RNG.randint(1, 10)))
    grades = []
    for code, name, order, (s, a, o, asst) in [("MIN", "Minor", 1, (8000, 3000, 6000, 0)), ("INT", "Intermediate", 2, (18000, 6000, 12000, 4000)),
                                                ("MAJ", "Major", 3, (35000, 12000, 25000, 8000)), ("SUP", "Super-major", 4, (75000, 22000, 45000, 15000))]:
        g, _ = SurgeryGrade.objects.get_or_create(hospital=h, code=code, defaults={"name": name, "sort_order": order})
        grades.append(g)
        for room, mult in [("", 1), ("general", 1), ("semi_private", 1.25), ("private", 1.6)]:
            OTTariff.objects.get_or_create(hospital=h, grade=g, room_category=room, defaults={"surgeon_fee": int(s * mult), "anaesthetist_fee": int(a * mult), "ot_charge": int(o * mult), "assistant_fee": int(asst * mult)})
    return grades


def inventory(h, users):
    cats = {}
    for name, code in [("Surgical consumables", "SURG"), ("Housekeeping", "HK"), ("Stationery", "STAT"), ("Linen", "LIN"), ("Implants", "IMP")]:
        cats[code], _ = ItemCategory.objects.get_or_create(hospital=h, code=code, defaults={"name": name})
    items = []
    for name, code, cat, uom, rate in [("Surgical gloves (sterile) size 7", "GLV7", "SURG", "pair", 18), ("Disposable syringe 5 ml", "SYR5", "SURG", "pcs", 4), ("IV cannula 20G", "IVC20", "SURG", "pcs", 22),
                                       ("Gauze roll 15 cm", "GAU15", "SURG", "roll", 45), ("Suture – polyglactin 2-0", "SUT20", "SURG", "pcs", 160), ("Surgical mask (3-ply)", "MSK3", "SURG", "pcs", 3),
                                       ("Floor disinfectant 5 L", "DIS5", "HK", "can", 650), ("Biomedical waste bags (yellow)", "BMWY", "HK", "pack", 220), ("A4 paper ream", "A4", "STAT", "ream", 260),
                                       ("Bedsheet (white)", "BED1", "LIN", "pcs", 320), ("Patient gown", "GOWN", "LIN", "pcs", 280), ("Knee implant – standard (demo)", "TKR1", "IMP", "set", 68000)]:
        it, _ = Item.objects.get_or_create(hospital=h, code=code, defaults={"category": cats[cat], "name": name, "unit_of_measure": uom, "min_stock_level": RNG.choice([20, 50, 100])})
        items.append((it, rate))
        StockLevel.objects.get_or_create(hospital=h, item=it, batch_number=f"B{code}01", defaults={"quantity_on_hand": RNG.choice([5, 12, 40, 150, 600]), "unit_cost": rate,
                                                                                                 "expiry_date": TODAY + timedelta(days=RNG.randint(90, 700))})
    main, _ = Store.objects.get_or_create(hospital=h, code="MAIN", defaults={"name": "Main Store", "is_main": True})
    Store.objects.get_or_create(hospital=h, code="OT", defaults={"name": "OT Sub-store"})
    Store.objects.get_or_create(hospital=h, code="ICU", defaults={"name": "ICU Sub-store"})
    vendor = Vendor.objects.filter(hospital=h, name__startswith="Western").first()
    rc, _ = RateContract.objects.get_or_create(hospital=h, contract_number="RC/2026/014", defaults={"vendor": vendor, "valid_from": TODAY - timedelta(days=120), "valid_to": TODAY + timedelta(days=24)})
    for it, rate in items[:6]:
        RateContractLine.objects.get_or_create(hospital=h, contract=rc, item=it, defaults={"rate": rate, "contracted_quantity": RNG.choice([2000, 5000, 10000])})
    pos = 0
    for i in range(14):
        po = PurchaseOrder.objects.create(hospital=h, po_number=f"PO26{i + 1:05d}", vendor=vendor, vendor_name=vendor.name, status=pick(["draft", "submitted", "submitted", "received"]),
                                          expected_delivery=TODAY + timedelta(days=RNG.randint(-6, 10)))
        PurchaseOrder.objects.filter(pk=po.pk).update(ordered_at=NOW - timedelta(days=RNG.randint(1, 50)))
        if po.status != "draft":
            PurchaseOrder.objects.filter(pk=po.pk).update(approved_by=(users.get("finance_manager") or [None])[0], approved_at=NOW - timedelta(days=RNG.randint(0, 30)))
        for it, rate in pick(items[:8], 3):
            line = RateContractLine.objects.filter(contract=rc, item=it).first()
            qty = RNG.choice([100, 200, 500, 1000])
            POItem.objects.create(purchase_order=po, item=it, ordered_quantity=qty, received_quantity=qty if po.status == "received" else 0, unit_cost=rate,
                                  contract_line=line, contract_rate=line.rate if line else None)
        pos += 1
    for dept in ["OT", "ICU", "General Ward – Male", "Emergency", "Maternity Ward"]:
        StoreIndent.objects.create(hospital=h, department=dept, from_store=main, items=[{"item": it.pk, "quantity": RNG.choice([10, 20, 50])} for it, _ in pick(items[:8], 3)],
                                   status=pick(["requested", "approved", "issued"]), requested_by=(users.get("nurse") or [None])[0])
    return {"items": len(items), "purchase_orders": pos}


def hr(h, users, docs):
    types = {}
    for code, name, quota, accrual, cf, short, paid in [("CL", "Casual leave", 12, "monthly", 0, "block", True), ("EL", "Earned leave", 18, "yearly", 30, "block", True),
                                                        ("SL", "Sick leave", 10, "yearly", 0, "warn", True), ("LWP", "Leave without pay", 0, "yearly", 0, "warn", False)]:
        types[code], _ = LeaveType.objects.get_or_create(hospital=h, code=code, defaults={"name": name, "annual_quota": quota, "accrual": accrual, "carry_forward_max": cf, "when_short": short, "is_paid": paid})
    staff = [u for us in users.values() for u in us] + [d.user for d in docs if d.user]
    emps = []
    for i, u in enumerate(staff):
        role = u.role.name if u.role else "Staff"
        e = Employee.objects.filter(user=u).first() or Employee.objects.create(hospital=h, user=u, employee_code=f"PNX{1001 + i}", designation=role if "Consultant" not in role else f"{role} – {u.department.name if u.department else ''}",
                                                                              department=u.department, date_of_joining=TODAY - timedelta(days=RNG.randint(120, 2400)),
                                                                              employment_type=pick(["permanent", "permanent", "contract"]) if "Consultant" not in role else "visiting")
        emps.append(e)
        basic = Decimal(RNG.choice([14000, 16000, 22000, 28000, 35000, 55000]))
        SalaryStructure.objects.get_or_create(hospital=h, employee=e, defaults={"basic": basic, "hra": basic * Decimal("0.4"), "special_allowance": basic * Decimal("0.2")})
        for back in range(1, 15):
            d = TODAY - timedelta(days=back)
            if d.weekday() == 6:
                continue
            st = RNG.choices(["present", "absent", "half_day", "leave"], weights=[88, 3, 3, 6])[0]
            Attendance.objects.get_or_create(employee=e, date=d, defaults={"hospital": h, "status": st, "check_in": timezone.make_aware(datetime.combine(d, time(8, RNG.randint(40, 59)))) if st != "absent" else None,
                                                                            "check_out": timezone.make_aware(datetime.combine(d, time(17, RNG.randint(0, 50)))) if st == "present" else None})
        if "Nurse" in role:
            for k in range(7):
                Shift.objects.get_or_create(employee=e, shift_date=TODAY + timedelta(days=k), defaults={"hospital": h, "shift_type": pick(["morning", "evening", "night"])})
    for e in pick(emps, 14):
        t = pick(list(types.values())[:3])
        start = TODAY + timedelta(days=RNG.randint(-20, 15))
        LeaveRequest.objects.create(hospital=h, employee=e, leave_type_master=t, start_date=start, end_date=start + timedelta(days=RNG.randint(0, 2)),
                                    status=pick(["pending", "approved", "approved", "rejected"]), reason=pick(["Family function", "Medical appointment", "Personal work", "Travel"]))
    for e in pick(emps, 8):
        OvertimeEntry.objects.create(hospital=h, employee=e, date=TODAY - timedelta(days=RNG.randint(1, 14)), hours=Decimal(pick([2, 3, 4, 6])), reason="Covering night shift",
                                     status=pick(["pending", "approved"]))
    return {"employees": len(emps)}


def ot(h, docs, pts, grades, users):
    surgeons = [d for d in docs if d.department.name in ("General Surgery", "Orthopaedics", "Obstetrics & Gynaecology")]
    anaes = next((d for d in docs if d.department.name == "Anaesthesiology"), docs[0])
    procs = [("Laparoscopic cholecystectomy", 2), ("Total knee replacement (right)", 3), ("Lower segment caesarean section", 2), ("Laparoscopic inguinal hernioplasty", 1),
             ("Laparoscopic appendicectomy", 1), ("ORIF – distal radius fracture", 2), ("Diagnostic hysteroscopy", 0), ("Excision of lipoma", 0)]
    inpatients = list(Admission.objects.filter(hospital=h, status="admitted"))
    RNG.shuffle(inpatients)  # each inpatient goes to theatre at most once
    by_dept = {"Lower segment caesarean section": "Obstetrics & Gynaecology", "Diagnostic hysteroscopy": "Obstetrics & Gynaecology",
               "Total knee replacement (right)": "Orthopaedics", "ORIF – distal radius fracture": "Orthopaedics"}
    emergencies = {"Lower segment caesarean section", "Laparoscopic appendicectomy", "ORIF – distal radius fracture"}
    n = 0
    for room in ["OT-1", "OT-2", "OT-3"]:
        t = time(8, 30)
        for k in range(RNG.randint(3, 4)):
            adm = inpatients.pop() if inpatients and chance(0.75) else None
            p = adm.patient if adm else pick(pts)
            name, g = pick([x for x in procs if suits(p, x[0])] or procs)
            surgeon = pick([d for d in surgeons if d.department.name == by_dept.get(name, "General Surgery")] or surgeons)
            req = SurgeryRequest.objects.create(hospital=h, patient=p, admission=adm, requested_by=surgeon.user, proposed_procedure=name, status="scheduled", surgery_grade=grades[g],
                                                priority="emergency" if (name in emergencies and chance(0.5)) else pick(["elective", "elective", "urgent"]))
            start = timezone.make_aware(datetime.combine(TODAY, t))
            end = start + timedelta(minutes=RNG.choice([60, 90, 120, 150]))
            status = "completed" if end < NOW else "in_progress" if start < NOW else "scheduled"
            sch = OTSchedule.objects.create(hospital=h, surgery_request=req, operation_theatre_room=room, surgeon=surgeon, anaesthetist=anaes.user, scheduled_start=start, scheduled_end=end,
                                            status=status, actual_start=start if status != "scheduled" else None, actual_end=end if status == "completed" else None)
            PreOpChecklist.objects.create(hospital=h, surgery_request=req, consent_obtained=True, fasting_confirmed=True, site_marked=True, asa_grade=pick(["I", "II", "II", "III"]), fit_for_surgery=True)
            if status != "scheduled":
                AnaesthesiaRecord.objects.create(hospital=h, ot_schedule=sch, anaesthesia_type=pick(["General anaesthesia (ETT)", "Spinal anaesthesia", "Regional block"]), anaesthetist=anaes.user,
                                                 asa_grade="II", airway_device="ETT 7.0", induction_at=start + timedelta(minutes=5),
                                                 vitals=[{"time": (start + timedelta(minutes=m)).strftime("%H:%M"), "hr": RNG.randint(68, 92), "bp": f"{RNG.randint(108, 132)}/{RNG.randint(64, 82)}", "spo2": RNG.randint(97, 100), "etco2": RNG.randint(32, 38)} for m in range(0, 60, 10)],
                                                 drugs=[{"time": (start + timedelta(minutes=5)).strftime("%H:%M"), "drug": "Propofol", "dose": "120 mg", "route": "IV"}, {"time": (start + timedelta(minutes=6)).strftime("%H:%M"), "drug": "Fentanyl", "dose": "100 mcg", "route": "IV"}],
                                                 fluids=[{"fluid": "Ringer lactate", "volume_ml": 1000}], estimated_blood_loss_ml=RNG.randint(50, 300))
            t = (end + timedelta(minutes=30)).time()
            n += 1
    for k in range(6):  # upcoming requests not yet scheduled
        name, g = pick(procs)
        SurgeryRequest.objects.create(hospital=h, patient=pick_for(pts, name), requested_by=pick(surgeons).user, proposed_procedure=name, status="requested", surgery_grade=grades[g])
    return n


def emergency(h, pts, users):
    n = 0
    complaints = [("Chest pain radiating to left arm", "2_emergent"), ("Road traffic accident – head injury", "1_resuscitation"), ("High fever with rigors", "3_urgent"),
                  ("Breathlessness, known asthmatic", "2_emergent"), ("Abdominal pain with vomiting", "3_urgent"), ("Minor cut on hand", "5_non_urgent"), ("Dog bite", "4_less_urgent"),
                  ("Fall from height – suspected fracture", "2_emergent"), ("Syncope at home", "3_urgent")]
    for i in range(26):
        cc, cat = pick(complaints)
        today = i < 9
        arrived = NOW - timedelta(minutes=RNG.randint(10, 360)) if today else days_ago(RNG.randint(1, 20))
        status = pick(["triaged", "in_treatment", "in_treatment"]) if today else pick(["admitted", "discharged", "discharged", "referred_out"])
        v = EDVisit.objects.create(hospital=h, patient=pick(pts), status=status, chief_complaint=cc, arrived_at=arrived, mode_of_arrival=pick(["ambulance", "walk_in", "private vehicle"]),
                                   is_mlc="accident" in cc.lower() or "fall" in cc.lower(), mlc_number=f"MLC/26/{100 + i}" if "accident" in cc.lower() else "",
                                   police_station="Chaturshringi Police Station" if "accident" in cc.lower() else "")
        Triage.objects.create(hospital=h, ed_visit=v, triage_category=cat, vitals_summary=f"HR {RNG.randint(70, 118)}, BP {RNG.randint(100, 160)}/{RNG.randint(60, 100)}, SpO2 {RNG.randint(91, 99)}%",
                              triaged_by=(users.get("nurse") or [None])[0], triaged_at=arrived + timedelta(minutes=4))
        n += 1
    return n


def icu(h):
    n = 0
    for adm in Admission.objects.filter(hospital=h, status="admitted", bed__bed_type="icu").select_related("bed"):
        ICUAdmission.objects.create(hospital=h, admission=adm, bed=adm.bed, ventilator_required=chance(0.3), admitted_at=adm.admitted_at, is_eligible=True,
                                    decision_reason="Haemodynamic monitoring required.", severity_scale="APACHE II", severity_score=RNG.randint(8, 24))
        n += 1
    return n


def bloodbank(h, pts):
    groups = ["A+", "B+", "O+", "AB+", "A-", "B-", "O-", "AB-"]
    donors = []
    for i in range(45):
        first, last, g = person()
        donors.append(Donor.objects.create(hospital=h, name=f"{first} {last}", blood_group=pick(groups[:4] * 3 + groups[4:]), phone=phone(), gender=g,
                                           last_donation_date=TODAY - timedelta(days=RNG.randint(10, 400)), weight_kg=RNG.randint(55, 85), haemoglobin=Decimal(str(round(RNG.uniform(12.5, 15.8), 1))),
                                           is_voluntary=chance(0.7)))
    for i in range(80):
        d = pick(donors)
        comp = pick(["whole_blood", "prbc", "prbc", "ffp", "platelets"])
        life = {"whole_blood": 35, "prbc": 42, "ffp": 365, "platelets": 5}[comp]
        # stock on the shelf is within its shelf life; a few platelet bags just past it were discarded
        coll = TODAY - timedelta(days=RNG.randint(0, min(30, life - 1)) if chance(0.92) else life + RNG.randint(1, 3))
        BloodUnit.objects.create(hospital=h, donor=d, blood_group=d.blood_group, component=comp, collection_date=coll, expiry_date=coll + timedelta(days=life),
                                 status="discarded" if coll + timedelta(days=life) < TODAY else pick(["available"] * 6 + ["reserved", "issued"]), unit_number=f"BU26{i + 1:05d}", volume_ml=350 if comp == "whole_blood" else 250, tti_screened=True,
                                 storage_location=pick(["Refrigerator 1", "Refrigerator 2", "Platelet agitator", "Deep freezer"]))
    for i in range(10):
        CrossMatchRequest.objects.create(hospital=h, patient=pick(pts), blood_group_required=pick(groups[:4]), component=pick(["prbc", "ffp", "platelets"]),
                                         status=pick(["pending", "matched", "matched"]), units_requested=RNG.randint(1, 3), urgency=pick(["routine", "urgent"]))
    return {"donors": len(donors), "units": 80}


def quality_and_support(h, pts, users):
    nurse = (users.get("nurse") or [None])[0]
    for itype, harm, desc in [("fall", "mild", "Patient slipped in bathroom; minor bruise on knee."), ("medication_error", "near_miss", "Wrong dose prepared, caught at bedside double-check."),
                              ("needlestick", "no_harm", "Needlestick injury to staff during IV cannulation."), ("pressure_ulcer", "moderate", "Stage II pressure injury noted on sacrum."),
                              ("equipment", "no_harm", "Infusion pump alarm failure reported."), ("adr", "mild", "Rash after IV antibiotic; drug stopped.")]:
        SafetyIncident.objects.create(hospital=h, incident_type=itype, harm=harm, patient=pick(pts), location=pick(["General Ward – Male", "Medical ICU", "Semi-Private Ward"]),
                                      occurred_at=NOW - timedelta(days=RNG.randint(1, 40)), description=desc, immediate_action="Attended immediately; supervisor informed.",
                                      reported_by=nurse, status=pick(["reported", "investigating", "rca_done", "closed"]))
    for itype, org in [("cauti", "E. coli"), ("ssi", "Staphylococcus aureus"), ("vap", "Klebsiella pneumoniae")]:
        HAIIncident.objects.create(hospital=h, patient=pick(pts), infection_type=itype, ward=pick(["Medical ICU", "Semi-Private Ward"]), onset_date=TODAY - timedelta(days=RNG.randint(2, 30)),
                                   organism=org, status=pick(["suspected", "confirmed", "resolved"]), reported_by=nurse)
    for no, kind in [("MH12 AB 1001", "als"), ("MH12 AB 1002", "bls"), ("MH12 AB 1003", "transport")]:
        Ambulance.objects.get_or_create(hospital=h, vehicle_number=no, defaults={"kind": kind, "driver_name": f"{person('male')[0]} {person()[1]}", "driver_phone": phone(),
                                                                                "status": pick(["available", "on_trip"]), "fitness_valid_until": TODAY + timedelta(days=200)})
    fleet = list(Ambulance.objects.filter(hospital=h))
    for i in range(8):
        AmbulanceTrip.objects.create(hospital=h, ambulance=fleet[i % len(fleet)], patient=pick(pts), caller_name=f"{person()[0]} {person()[1]}", caller_phone=phone(), pickup_address=f"{pick(['Baner', 'Aundh', 'Wakad'])}, Pune",
                                     destination="Polynexus Demo Multispeciality Hospital", chief_complaint=pick(["Chest pain", "Accident", "Breathlessness"]), status=pick(["completed", "completed", "en_route", "dispatched"]),
                                     requested_at=NOW - timedelta(hours=RNG.randint(1, 200)), eta_minutes=RNG.randint(8, 25))
    for name, cat, cost in [("Ventilator – ICU bed 1", "Ventilator", 1450000), ("Defibrillator – ED", "Defibrillator", 380000), ("Multipara monitor – ICU", "Monitor", 210000),
                            ("Anaesthesia workstation – OT-1", "Anaesthesia", 1850000), ("Autoclave – CSSD", "Sterilisation", 950000), ("Infusion pump", "Infusion", 65000)]:
        EquipmentAsset.objects.get_or_create(hospital=h, asset_tag=f"BME-{name[:3].upper()}{RNG.randint(100, 999)}", defaults={
            "name": name, "category": cat, "make": pick(["GE Healthcare", "Philips", "Mindray", "Draeger"]), "location": name.split("–")[-1].strip(), "purchase_date": TODAY - timedelta(days=RNG.randint(200, 1500)),
            "purchase_cost": cost, "warranty_until": TODAY + timedelta(days=RNG.randint(-100, 400)), "amc_until": TODAY + timedelta(days=RNG.randint(20, 300)), "pm_frequency_days": 90,
            "last_pm_on": TODAY - timedelta(days=RNG.randint(10, 100)), "is_critical": cat in ("Ventilator", "Defibrillator", "Anaesthesia"), "status": pick(["in_use", "in_use", "in_use", "maintenance"])})
    for bed in Bed.objects.filter(hospital=h, status="cleaning")[:3]:
        HousekeepingTask.objects.create(hospital=h, task_type="terminal", location=str(bed), bed=bed, priority="high", status=pick(["pending", "in_progress"]), due_by=NOW + timedelta(hours=1))


def run(ctx):
    h, docs, pts, users = ctx["h"], ctx["docs"], ctx["pts"], ctx["users"]
    out = {**tpa(h, pts, docs, users)}
    grades = finance(h, users)
    out.update(inventory(h, users))
    out.update(hr(h, users, docs))
    out["surgeries_today"] = ot(h, docs, pts, grades, users)
    out["ed_visits"] = emergency(h, pts, users)
    out["icu"] = icu(h)
    out.update(bloodbank(h, pts))
    quality_and_support(h, pts, users)
    return out
