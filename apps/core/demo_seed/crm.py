"""CRM: enquiries (leads) across stages and sources, surgical treatment
estimates, calls and callbacks, WhatsApp conversations and broadcasts,
referral doctors, packages / camps / corporate clients, feedback & NPS,
workflows and tasks."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.appointments.models import Waitlist
from apps.automation.models import Task, Workflow, WorkflowRun, WorkflowStep
from apps.communications.models import BroadcastCampaign, Message, Template, Thread
from apps.enquiries.models import Enquiry, EnquiryStageChange, TreatmentEstimate
from apps.feedback.models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask
from apps.packages.models import CampRegistration, Campaign, CorporateClient, HealthPackage
from apps.referrals.models import FieldVisit, ReferralRecord, ReferringDoctor
from apps.telephony.models import Call, CallbackTask

from .common import NOW, RNG, TODAY, at_today, chance, days_ago, first_page_patients, person, phone, pick, pick_for

SERVICES = {
    "Cardiology": ["Angiography consultation", "2D Echo & cardiac check-up", "Second opinion – bypass surgery"],
    "Orthopaedics": ["Knee replacement enquiry", "Back pain consultation", "Sports injury – ACL tear"],
    "Obstetrics & Gynaecology": ["Maternity package", "Antenatal consultation", "Laparoscopic hysterectomy enquiry"],
    "General Surgery": ["Gallbladder stone surgery", "Hernia repair", "Piles / fistula treatment"],
    "General Medicine": ["Fever consultation", "Diabetes management", "Executive health check-up"],
    "Paediatrics": ["Child vaccination", "Paediatric consultation"],
    "Neurology": ["Migraine consultation", "Stroke follow-up"],
    "Nephrology": ["Dialysis enquiry", "Kidney function consultation"],
    "Ophthalmology": ["Cataract surgery enquiry", "LASIK consultation"],
}
VALUES = {"Cardiology": (25000, 260000), "Orthopaedics": (15000, 320000), "Obstetrics & Gynaecology": (12000, 95000), "General Surgery": (25000, 110000),
          "Ophthalmology": (18000, 85000), "Nephrology": (4000, 60000)}
STAGES = ["new"] * 22 + ["contacted"] * 30 + ["scheduled"] * 26 + ["visited"] * 22 + ["completed"] * 34 + ["follow_up"] * 18 + ["lost"] * 18
SOURCES = ["website"] * 5 + ["ivr"] * 5 + ["whatsapp"] * 4 + ["google"] * 4 + ["meta"] * 3 + ["walk_in"] * 3 + ["referral"] * 3 + ["other"]


def enquiries(h, depts, docs, users):
    execs = users.get("crm_executive", []) + users.get("call_centre_executive", []) + users.get("crm_manager", [])
    out = []
    for i in range(180):
        dept_name = pick(list(SERVICES))
        first, last, _g = person("female" if dept_name == "Obstetrics & Gynaecology" else None)
        stage = pick(STAGES)
        created = timezone.now() - timedelta(hours=RNG.randint(1, 60)) if stage == "new" else days_ago(RNG.randint(1, 60))
        lo, hi = VALUES.get(dept_name, (800, 6000))
        e = Enquiry.objects.create(hospital=h, name=f"{first} {last}", mobile=phone(), email=f"{first}.{last}{i}".lower() + "@example.com" if chance(0.4) else "",
                                   source=pick(SOURCES), campaign=pick(["", "", "Knee Pain Awareness – Oct", "Heart Health Month", "Monsoon Fever Care"]),
                                   utm_source=pick(["", "google", "facebook", "instagram"]), department=depts[dept_name],
                                   consulting_doctor=next((d for d in docs if d.department_id == depts[dept_name].pk), None),
                                   service_requested=pick(SERVICES[dept_name]), urgency=pick(["normal", "normal", "high", "low", "urgent"]), score=RNG.randint(25, 95),
                                   stage=stage, assigned_to=pick(execs) if execs else None, estimated_value=Decimal(RNG.randrange(lo, hi, 500)),
                                   follow_up_date=TODAY + timedelta(days=RNG.randint(-2, 7)) if stage in ("contacted", "follow_up", "scheduled") else None,
                                   sla_due_at=(created + timedelta(hours=pick([2, 4, 24, 48, 72]))) if stage == "new" else None, first_contacted_at=created + timedelta(minutes=RNG.randint(4, 90)) if stage != "new" else None,
                                   lost_reason=pick(["cost", "location", "went_elsewhere", "no_response", "not_interested"]) if stage == "lost" else "",
                                   notes=pick(["Prefers morning appointment.", "Asked about cashless insurance.", "Wants estimate on WhatsApp.", "Referred by family member.", ""]))
        Enquiry.objects.filter(pk=e.pk).update(created_at=created)
        if stage != "new":
            EnquiryStageChange.objects.create(hospital=h, enquiry=e, from_stage="new", to_stage=stage, changed_by=e.assigned_to)
        out.append(e)
    return out


def estimates(h, depts, docs, pts, enqs):
    procs = [("Cardiology", "Coronary angioplasty (PTCA) – single stent", 85000, 45000, 18000), ("Orthopaedics", "Total knee replacement – unilateral", 95000, 55000, 22000),
             ("General Surgery", "Laparoscopic cholecystectomy", 35000, 25000, 12000), ("Obstetrics & Gynaecology", "Caesarean delivery package", 30000, 18000, 15000),
             ("Ophthalmology", "Phaco cataract surgery with IOL", 18000, 8000, 0), ("General Surgery", "Laparoscopic hernioplasty", 32000, 22000, 9000)]
    n = 0
    for i in range(34):
        dept, proc, surgeon, ot, room = pick(procs)
        stay = RNG.randint(1, 5)
        cat = pick(["general", "semi_private", "private", "deluxe"])
        mult = {"general": 1, "semi_private": 1.3, "private": 1.7, "deluxe": 2.2}[cat]
        meds, impl = RNG.randrange(8000, 30000, 500), RNG.randrange(0, 60000, 1000) if "stent" in proc or "knee" in proc else RNG.randrange(2000, 9000, 500)
        total = Decimal(int(surgeon * mult + ot + room * stay * mult / 2 + meds + impl))
        mode = pick(["cash", "insurance", "insurance", "govt_scheme", "corporate"])
        stage = pick(["advised", "counseling", "estimate_shared", "estimate_shared", "preauth_in_progress", "scheduled", "converted", "converted", "dropped"])
        TreatmentEstimate.objects.create(hospital=h, patient=pick_for(pts, proc, dept), enquiry=pick(enqs) if chance(0.5) else None, doctor=next((d for d in docs if d.department.name == dept), None),
                                         department=depts[dept], procedure_name=proc, room_category=cat, stay_days=stay, surgeon_fee=int(surgeon * mult), ot_charges=ot,
                                         room_charges=int(room * stay * mult / 2), medicines_estimate=meds, implants_investigations=impl, total_estimate=total, payment_mode=mode,
                                         tpa_name=pick(["Medi Assist TPA", "Paramount Health Services", "Star Health"]) if mode == "insurance" else "",
                                         insurance_preauth_status=pick(["submitted", "approved", "query_raised"]) if mode == "insurance" else "not_applicable",
                                         stage=stage, drop_reason=pick(["cost_high", "postponed", "went_elsewhere"]) if stage == "dropped" else "",
                                         valid_until=TODAY + timedelta(days=RNG.randint(5, 30)))
        n += 1
    return n


def calls(h, depts, pts, enqs, users):
    ops = users.get("call_centre_executive", []) + users.get("crm_executive", [])
    n = 0
    for back in range(30, -1, -1):
        for _ in range(RNG.randint(8, 16) if back else 12):
            inbound = chance(0.72)
            status = RNG.choices(["answered", "missed", "rnr", "busy"], weights=[78, 12, 6, 4])[0] if inbound else RNG.choices(["answered", "rnr", "busy"], weights=[70, 20, 10])[0]
            since_8 = max(10, int((timezone.now() - at_today(8, 0)).total_seconds() // 60))  # today's calls only after 8 am
            start = days_ago(back) if back else timezone.now() - timedelta(minutes=RNG.randint(5, since_8))
            dur = RNG.randint(45, 540) if status == "answered" else 0
            p = pick(pts) if chance(0.7) else None
            c = Call.objects.create(hospital=h, direction="inbound" if inbound else "outbound", status=status, from_number=p.mobile if (p and inbound) else phone() if inbound else "+912067000000",
                                    to_number="+912067000000" if inbound else (p.mobile if p else phone()), patient=p, enquiry=pick(enqs) if chance(0.25) else None,
                                    department=pick(list(depts.values())), operator=pick(ops) if ops and (status == "answered" or inbound) else None,  # a missed inbound call still rang an agent
                                    started_at=start,
                                    answered_at=start + timedelta(seconds=8) if status == "answered" else None, ended_at=start + timedelta(seconds=dur + 10), duration_seconds=dur,
                                    consent_recorded=status == "answered", ivr_path=pick(["1>2", "1>1", "2>1", "3"]) if inbound else "",
                                    call_reason=pick(["opd", "opd", "opd", "surgical", "report", "billing", "second_opinion", "complaint"]),
                                    notes=pick(["Appointment booked for tomorrow.", "Asked for report status – shared on WhatsApp.", "Enquired about knee replacement cost.", "Billing query resolved.", ""]),
                                    provider_name="demo", provider_call_id=f"DEMO-{back}-{n}")
            n += 1
            if status in ("missed", "rnr") and inbound and back == 0:
                CallbackTask.objects.create(hospital=h, call=c, source="missed_call", patient=p, phone_number=c.from_number, department=c.department, owner=pick(ops) if ops and chance(0.6) else None,
                                            status=pick(["pending", "pending", "in_progress", "done"]), sla_due_at=timezone.now() + timedelta(minutes=RNG.randint(-25, 50)), attempt_count=RNG.randint(0, 2))
    return n


def conversations(h, pts, users):
    agent = (users.get("crm_executive") or [None])[0]
    tpl = []
    for name, purpose, body in [("Appointment confirmation", "appointment_confirmation", "Dear {{name}}, your appointment with Dr. {{doctor}} is confirmed for {{date}} at {{time}}. Reply 1 to confirm."),
                                ("Report ready", "report_ready", "Dear {{name}}, your lab report is ready. View it securely here: {{link}}"),
                                ("Follow-up reminder", "follow_up", "Hello {{name}}, this is a reminder for your follow-up visit with Dr. {{doctor}} on {{date}}."),
                                ("Health camp invite", "marketing", "Free bone-density screening camp this Sunday, 9 am – 1 pm at Polynexus Demo Multispeciality Hospital. Reply YES to register.")]:
        t, _ = Template.objects.get_or_create(hospital=h, name=name, channel="whatsapp", language="en", defaults={"purpose": purpose, "body": body})
        tpl.append(t)
    convo = [("inbound", "Hi, I want to book an appointment with an orthopaedic doctor for knee pain."),
             ("outbound", "Hello! Dr. Vikram Patil is available tomorrow at 10:30 am or 4:00 pm. Which suits you?"),
             ("inbound", "10:30 am please."), ("outbound", "Booked ✅ Tomorrow 10:30 am with Dr. Vikram Patil, Orthopaedics. Please carry your previous X-rays."),
             ("inbound", "Thank you. Is parking available?"), ("outbound", "Yes, free parking is available in the basement for patients.")]
    n = 0
    for i, p in enumerate(first_page_patients(h)[:20]):  # the inbox resolves names from the first patient page
        th, _ = Thread.objects.get_or_create(hospital=h, patient=p, channel="whatsapp", defaults={"owner": agent, "status": pick(["open", "open", "closed"])})
        k = RNG.randint(2, len(convo))
        last = None
        day_back = RNG.randint(0, 6)  # one conversation, one day, messages in order
        for j, (direction, body) in enumerate(convo[:k]):
            when = NOW - timedelta(days=day_back, minutes=(k - j) * 7)
            Message.objects.create(hospital=h, patient=p, channel="whatsapp", direction=direction, body=body, status="read" if direction == "outbound" else "received",
                                   is_read=chance(0.8), sent_by=agent if direction == "outbound" else None, sent_at=when)
            last = when
        Thread.objects.filter(pk=th.pk).update(last_message_at=last, last_inbound_at=last)
        n += 1
    for title, aud, status, total in [("Heart Health Month – free ECG", "all_patients", "completed", 1240), ("Diabetes follow-up reminder", "chronic_care", "completed", 312),
                                      ("Senior citizen health check offer", "senior_citizens", "scheduled", 418), ("Re-engage unconverted surgical leads", "unconverted_leads", "draft", 0)]:
        sent = int(total * 0.97) if status == "completed" else 0
        BroadcastCampaign.objects.create(hospital=h, title=title, channel="whatsapp", target_audience=aud, template=tpl[3], custom_message=tpl[3].body, status=status,
                                         total_recipients=total, sent_count=sent, delivered_count=int(sent * 0.95), read_count=int(sent * 0.71), failed_count=total - sent if status == "completed" else 0,
                                         scheduled_for=NOW + timedelta(days=2) if status == "scheduled" else None, created_by=agent)
    return n


def referrals(h, depts, pts, users):
    rep = (users.get("marketing_manager") or [None])[0]
    clinics = ["Sai Clinic", "Shree Family Clinic", "Care Polyclinic", "Arogya Nursing Home", "Lifeline Clinic", "Sanjeevani Hospital", "Om Diagnostics", "Health First Clinic"]
    docs = []
    for i in range(26):
        first, last, _g = person()
        docs.append(ReferringDoctor.objects.create(hospital=h, name=f"{first} {last}", speciality=pick(["General Practitioner", "Physician", "Paediatrician", "Gynaecologist", "Orthopaedic surgeon"]),
                                                   clinic_name=pick(clinics), mobile=phone(), city=pick(["Pune", "Pimpri-Chinchwad", "Talegaon", "Lonavala"]),
                                                   tier=pick(["gold", "silver", "silver", "bronze", "bronze"])))
    n = 0
    for _ in range(110):
        r = ReferralRecord.objects.create(hospital=h, referring_doctor=pick(docs), patient=pick(pts), department=pick(list(depts.values())[:12]),
                                          attributed_revenue=Decimal(RNG.randrange(1500, 180000, 500)), commission_percentage=Decimal(pick([0, 5, 8, 10])),
                                          status=pick(["pending", "converted", "converted", "paid"]))
        ReferralRecord.objects.filter(pk=r.pk).update(referred_at=days_ago(RNG.randint(0, 90)))
        n += 1
    for _ in range(30):
        FieldVisit.objects.create(hospital=h, referring_doctor=pick(docs), visited_by=rep, visit_date=TODAY - timedelta(days=RNG.randint(0, 45)),
                                  notes=pick(["Shared cardiac package brochure.", "Discussed referral TAT and report sharing on WhatsApp.", "Invited for CME on diabetes management."]),
                                  outcome=pick(["positive", "follow_up", "neutral"]))
    rank_tiers(h)
    return {"referring_doctors": len(docs), "referrals": n}


def rank_tiers(h):
    """Relationship tier in line with the revenue each referrer brings: top 4 gold, next 8 silver."""
    from django.db.models import Sum
    ranked = ReferringDoctor.objects.filter(hospital=h).annotate(rev=Sum("referrals__attributed_revenue")).order_by("-rev")
    for i, d in enumerate(ranked):
        ReferringDoctor.objects.filter(pk=d.pk).update(tier="gold" if i < 4 else "silver" if i < 12 else "bronze")


def packages(h, pts):
    pk = [("Executive Health Check", "EHC", "full_body", 4999, ["CBC", "Lipid profile", "HbA1c", "LFT", "KFT", "ECG", "Chest X-ray", "Physician consult"]),
          ("Cardiac Screening", "CARD", "cardiac", 3499, ["ECG", "2D Echo", "Lipid profile", "TMT", "Cardiologist consult"]),
          ("Women's Wellness", "WW", "full_body", 3999, ["CBC", "Thyroid", "Pap smear", "USG pelvis", "Gynaecologist consult"]),
          ("Senior Citizen Check", "SCC", "full_body", 2999, ["CBC", "Blood sugar", "KFT", "Vitamin D", "Bone density", "Physician consult"]),
          ("Normal Delivery Package", "NDP", "maternity", 45000, ["2-day stay", "Delivery charges", "Paediatrician visit"]),
          ("Caesarean Delivery Package", "CDP", "maternity", 85000, ["4-day stay", "OT charges", "Anaesthesia", "Paediatrician visit"]),
          ("Knee Replacement Package", "TKR", "ortho", 210000, ["5-day stay", "Implant (standard)", "Surgeon & OT", "Physiotherapy x 5"]),
          ("Cataract Surgery Package", "CAT", "day_care", 28000, ["Phaco surgery", "Foldable IOL", "Medicines", "Follow-up x 2"]),
          ("Diabetes Care Annual", "DCA", "medical", 6999, ["4 physician visits", "HbA1c x 4", "Diet counselling", "Eye & foot screening"])]
    for name, code, cat, price, inc in pk:
        HealthPackage.objects.get_or_create(hospital=h, code=code, defaults={"name": name, "category": cat, "price": price, "included_tests": inc,
                                                                             "length_of_stay_days": 0 if cat in ("full_body", "cardiac", "medical") else 3})
    camps = []
    for name, ctype, budget, spend, status, start in [("Bone Density Camp – Baner", "health_camp", 60000, 52000, "completed", 40), ("Heart Health Month", "digital_ad", 150000, 118000, "active", 14),
                                                      ("Diabetes Screening – IT Park Hinjewadi", "corporate_tieup", 45000, 38000, "completed", 25), ("Monsoon Fever Care", "digital_ad", 80000, 21000, "active", 5),
                                                      ("Senior Citizens Day Camp", "health_camp", 40000, 0, "planned", -10)]:
        camps.append(Campaign.objects.create(hospital=h, name=name, campaign_type=ctype, budget=budget, actual_spend=spend, status=status,
                                             start_date=TODAY - timedelta(days=start), end_date=TODAY - timedelta(days=start - 7)))
    corps = []
    for name, model, emp, disc in [("Infosphere Technologies Pvt Ltd", "per_employee", 4200, 15), ("Deccan Auto Components Ltd", "flat_annual", 1800, 10),
                                   ("Sahyadri Co-operative Bank", "credit_line", 650, 12), ("Western Logistics India", "per_employee", 900, 10),
                                   ("Pune Metro Builders", "flat_annual", 1200, 8), ("Nexa Software Services", "per_employee", 2600, 15)]:
        first, last, _g = person()
        corps.append(CorporateClient.objects.create(hospital=h, name=name, contact_person=f"{first} {last}", contact_phone=phone(), contact_email=f"hr@{name.split()[0].lower()}.example.com",
                                                    campaign=camps[2] if "Info" in name else None, billing_model=model, discount_percent=disc, contract_start=TODAY - timedelta(days=RNG.randint(60, 400)),
                                                    contract_end=TODAY + timedelta(days=RNG.randint(30, 300)), employee_count=emp))
    n = 0
    for c in camps[:4]:
        for _ in range(RNG.randint(18, 40)):
            first, last, _g = person()
            stage = pick(["registered", "attended", "attended", "opd_converted", "ipd_converted"])
            CampRegistration.objects.create(hospital=h, campaign=c, patient_name=f"{first} {last}", mobile=phone(), stage=stage,
                                            revenue_generated=Decimal(RNG.randrange(800, 4000, 100) if stage == "opd_converted" else RNG.randrange(30000, 150000, 1000) if stage == "ipd_converted" else 0),
                                            corporate_client=corps[0] if c.campaign_type == "corporate_tieup" else None)
            n += 1
    return {"packages": len(pk), "campaigns": len(camps), "corporate_clients": len(corps), "camp_registrations": n}


def feedback(h, depts, docs, pts, users):
    owner = (users.get("crm_manager") or [None])[0]
    comments = {"promoter": ["Doctor explained everything patiently.", "Very clean hospital and quick billing.", "Excellent nursing care during my mother's stay."],
                "passive": ["Good treatment but waiting time was long.", "Overall fine, parking was difficult."],
                "detractor": ["Waited over an hour beyond my appointment time.", "Billing took too long at discharge.", "Reports were delayed by a day."]}
    n = 0
    for _ in range(150):
        d = pick([x for x in docs if x.department.name not in ("Radiology", "Pathology", "Anaesthesiology")])
        p = pick(pts)
        fr = FeedbackRequest.objects.create(hospital=h, patient=p, doctor=d, department=d.department, status="responded" if chance(0.78) else "sent")
        if fr.status == "responded":
            score = RNG.choices(range(0, 11), weights=[1, 1, 1, 1, 2, 3, 4, 7, 12, 20, 26])[0]
            cat = "promoter" if score >= 9 else "passive" if score >= 7 else "detractor"
            r = NPSResponse.objects.create(hospital=h, feedback_request=fr, patient=p, doctor=d, department=d.department, score=score, category=cat, comment=pick(comments[cat]))
            NPSResponse.objects.filter(pk=r.pk).update(created_at=days_ago(RNG.randint(0, 45)))
            if cat == "detractor":
                # the app opens a recovery task for every detractor (signal); set its owner / progress
                ServiceRecoveryTask.objects.update_or_create(nps_response=r, defaults={"hospital": h, "owner": owner, "status": pick(["pending", "in_progress", "resolved"]),
                                                                                       "sla_due_at": NOW + timedelta(hours=RNG.randint(-12, 24))})
            n += 1
    for desc, status in [("Long waiting time at OPD registration counter.", "investigating"), ("Discharge billing delayed by 3 hours.", "open"),
                         ("Cafeteria food was cold.", "closed"), ("Lab report sent to wrong WhatsApp number.", "closed"), ("Rude behaviour at pharmacy counter.", "investigating")]:
        Complaint.objects.create(hospital=h, patient=pick(pts), department=pick(list(depts.values())), description=desc, status=status, owner=owner,
                                 root_cause="Staffing gap during peak hours." if status == "closed" else "")
    return n


def automation(h, users, depts):
    owner = (users.get("crm_manager") or [None])[0]
    flows = [("Missed call → callback in 15 min", "missed_call", [("create_task", "Call back the patient"), ("send_whatsapp", "Sorry we missed your call")]),
             ("New website enquiry → WhatsApp welcome", "enquiry_created", [("send_whatsapp", "Welcome message"), ("create_task", "Qualify lead")]),
             ("No-show → rebook offer", "appointment_no_show", [("wait_delay", "Wait 2 hours"), ("send_whatsapp", "Rebook link")]),
             ("NPS detractor → service recovery", "nps_detractor", [("create_task", "Call the patient within 24 h"), ("escalate", "Escalate to CRM manager")])]
    for name, trig, steps in flows:
        # created switched off: the engine would otherwise fire on events created later in the seed
        wf, created = Workflow.objects.get_or_create(hospital=h, name=name, defaults={"trigger_type": trig, "is_active": False, "created_by": owner})
        if created:
            for i, (action, title) in enumerate(steps, start=1):
                WorkflowStep.objects.create(workflow=wf, order=i, step_type="action", action_type=action, title=title,
                                            config={"purpose": "follow_up"} if action == "send_whatsapp" else {"minutes": 120} if action == "wait_delay" else {})
            for _ in range(RNG.randint(6, 18)):
                run = WorkflowRun.objects.create(hospital=h, workflow=wf, trigger_event=trig, status=pick(["success", "success", "success", "waiting", "failed"]), context={})
                WorkflowRun.objects.filter(pk=run.pk).update(executed_at=NOW - timedelta(minutes=RNG.randint(30, 18000)))  # executed_at is auto_now_add
    execs = users.get("crm_executive", []) + users.get("call_centre_executive", [])
    for title in ["Call back Mr. Kulkarni about TKR estimate", "Share cardiac package brochure with Sai Clinic", "Confirm tomorrow's health camp volunteers",
                  "Follow up on pending pre-auth documents", "Send report link to patient", "Re-engage lost lead – cataract surgery", "Verify corporate employee list – Infosphere",
                  "Update referral payout statement", "Schedule CME invitation calls", "Resolve NPS detractor feedback"]:
        for _ in range(2):
            Task.objects.create(hospital=h, title=title, owner=pick(execs) if execs else None, department=pick(list(depts.values())[:6]),
                                status=pick(["pending", "pending", "in_progress", "done"]), priority=pick(["normal", "high", "urgent", "low"]),
                                due_at=NOW + timedelta(hours=RNG.randint(-20, 72)), created_by=owner)


def waitlist(h, docs, pts):
    Waitlist.objects.filter(hospital=h).delete()  # the base seed's entries point at placeholder patients
    for p in first_page_patients(h)[21:25]:  # the waitlist resolves names from the first patient page
        d = pick([x for x in docs if x.department.name in ("Cardiology", "Orthopaedics", "General Medicine")])
        Waitlist.objects.create(hospital=h, patient=p, doctor=d, department=d.department, preferred_date=TODAY + timedelta(days=RNG.randint(1, 3)), notes="Prefers a morning slot.")


def run(ctx):
    h, depts, docs, pts, users = ctx["h"], ctx["depts"], ctx["docs"], ctx["pts"], ctx["users"]
    enqs = enquiries(h, depts, docs, users)
    out = {"enquiries": len(enqs), "estimates": estimates(h, depts, docs, pts, enqs), "calls": calls(h, depts, pts, enqs, users),
           "whatsapp_threads": conversations(h, pts, users), **referrals(h, depts, pts, users), **packages(h, pts), "nps": feedback(h, depts, docs, pts, users)}
    automation(h, users, depts)
    waitlist(h, docs, pts)
    return out
