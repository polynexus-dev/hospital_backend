CODES = [
    ("Code Blue", "Blue", "Medical emergency / cardiac arrest (adult)", ["doctor", "nurse", "icu_staff", "anaesthetist"],
     "Start CPR, call code team, bring crash cart and defibrillator, follow ACLS algorithm."),
    ("Code Pink", "Pink", "Infant / child abduction", ["front_desk", "nurse", "admin"], "Seal exits, search, inform security and police."),
    ("Code Red", "Red", "Fire", ["admin", "nurse", "front_desk"], "RACE: Rescue, Alarm, Contain, Extinguish/Evacuate."),
    ("Code Yellow", "Yellow", "Mass casualty / disaster", ["doctor", "nurse", "admin"], "Activate disaster plan, set up triage area, recall staff."),
    ("Code Grey", "Grey", "Violent / combative person", ["admin", "front_desk"], "Call security, de-escalate, keep staff and patients safe."),
    ("Code Orange", "Orange", "Hazardous material spill", ["admin", "nurse"], "Isolate area, use spill kit, inform infection control."),
    ("Code Purple", "Purple", "Paediatric medical emergency", ["doctor", "nurse", "icu_staff"], "Paediatric resuscitation team response, PALS algorithm."),
]

CHECKLISTS = [
    ("Crash cart daily check", "crash_cart", [
        "Defibrillator charged and self-test passed", "Adrenaline, atropine, amiodarone stocked to par level", "Airway kit (laryngoscope, ET tubes, bag-valve-mask) complete",
        "Suction working", "No expired drugs or consumables", "Seal intact and number recorded",
    ]),
    ("Emergency medication protocol — anaphylaxis", "emergency_protocol", [
        "Stop the suspected agent", "Adrenaline 0.5 mg IM (1:1000) given", "Airway secured, high-flow oxygen", "IV access + fluid bolus",
        "Antihistamine and steroid given", "Event documented and ADR reported",
    ]),
    ("Monthly pharmacy stock audit", "stock_audit", [
        "Physical stock matches system stock", "Near-expiry (<90 days) items segregated", "High-risk medicines stored separately and labelled",
        "LASA medicines segregated", "Cold-chain temperature log complete", "Narcotic register reconciled",
    ]),
]


def seed_hospital(hospital, get_model=None):
    if get_model is None:
        from django.apps import apps as django_apps

        get_model = django_apps.get_model
    EmergencyCode = get_model("quality", "EmergencyCode")
    ChecklistTemplate = get_model("quality", "ChecklistTemplate")
    if not EmergencyCode.objects.filter(hospital=hospital).exists():
        EmergencyCode.objects.bulk_create([
            EmergencyCode(hospital=hospital, code=c, color=col, meaning=m, responder_roles=roles, protocol=p) for c, col, m, roles, p in CODES
        ])
    if not ChecklistTemplate.objects.filter(hospital=hospital).exists():
        ChecklistTemplate.objects.bulk_create([
            ChecklistTemplate(hospital=hospital, name=n, purpose=p, items=[{"text": t, "required": True} for t in items]) for n, p, items in CHECKLISTS
        ])
