"""
Validated risk tools (NABH COP.9.a, COP.5.b) and rehab scales (COP.11.a).
Each scorer takes the submitted answers dict and returns (score, level).
Item keys/weights follow the published instruments; unknown keys are
ignored and missing items count as the lowest-risk option.
"""


def _i(answers, key, default=0):
    try:
        return int(answers.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


def morse_fall(a):
    score = (
        (25 if a.get("history_of_falling") else 0)
        + (15 if a.get("secondary_diagnosis") else 0)
        + {"none": 0, "crutches_cane_walker": 15, "furniture": 30}.get(a.get("ambulatory_aid", "none"), 0)
        + (20 if a.get("iv_or_heparin_lock") else 0)
        + {"normal": 0, "weak": 10, "impaired": 20}.get(a.get("gait", "normal"), 0)
        + (15 if a.get("forgets_limitations") else 0)
    )
    level = "high" if score >= 45 else "moderate" if score >= 25 else "low"
    return score, level


BRADEN_ITEMS = ("sensory_perception", "moisture", "activity", "mobility", "nutrition", "friction_shear")


def braden(a):
    # Each item 1–4 (friction/shear 1–3); lower = higher risk. Missing items
    # default to the healthiest value so a partial form under-, not over-alarms.
    maxes = {"friction_shear": 3}
    score = sum(min(max(_i(a, k, maxes.get(k, 4)), 1), maxes.get(k, 4)) for k in BRADEN_ITEMS)
    if score <= 9:
        level = "very_high"
    elif score <= 12:
        level = "high"
    elif score <= 14:
        level = "moderate"
    elif score <= 18:
        level = "mild"
    else:
        level = "no_risk"
    return score, level


CAPRINI_POINTS = {
    # 1 point
    "age_41_60": 1, "minor_surgery": 1, "bmi_over_25": 1, "swollen_legs": 1, "varicose_veins": 1,
    "pregnancy_or_postpartum": 1, "oral_contraceptives": 1, "sepsis_last_month": 1, "copd": 1, "bed_rest_medical": 1,
    # 2 points
    "age_61_74": 2, "major_surgery_over_45min": 2, "laparoscopic_over_45min": 2, "malignancy": 2,
    "confined_to_bed_over_72h": 2, "immobilizing_cast": 2, "central_venous_access": 2,
    # 3 points
    "age_75_plus": 3, "history_of_vte": 3, "family_history_vte": 3, "factor_v_leiden": 3, "heparin_induced_thrombocytopenia": 3,
    # 5 points
    "stroke_last_month": 5, "elective_arthroplasty": 5, "hip_pelvis_leg_fracture": 5, "acute_spinal_cord_injury": 5,
}


def caprini(a):
    score = sum(p for k, p in CAPRINI_POINTS.items() if a.get(k))
    if score >= 5:
        level = "highest"
    elif score >= 3:
        level = "high"
    elif score >= 2:
        level = "moderate"
    elif score >= 1:
        level = "low"
    else:
        level = "very_low"
    return score, level


def news2(a):
    """National Early Warning Score 2 (COP.12.b critical intervention
    trigger). Expects numeric vitals."""
    s = 0
    rr = _i(a, "respiratory_rate", 16)
    s += 3 if rr <= 8 else 1 if rr <= 11 else 0 if rr <= 20 else 2 if rr <= 24 else 3
    spo2 = _i(a, "spo2", 98)
    s += 3 if spo2 <= 91 else 2 if spo2 <= 93 else 1 if spo2 <= 95 else 0
    s += 2 if a.get("on_supplemental_oxygen") else 0
    try:
        temp = float(a.get("temperature_c", 37) or 37)
    except (TypeError, ValueError):
        temp = 37.0
    s += 3 if temp <= 35.0 else 1 if temp <= 36.0 else 0 if temp <= 38.0 else 1 if temp <= 39.0 else 2
    sbp = _i(a, "systolic_bp", 120)
    s += 3 if sbp <= 90 else 2 if sbp <= 100 else 1 if sbp <= 110 else 0 if sbp <= 219 else 3
    hr = _i(a, "heart_rate", 80)
    s += 3 if hr <= 40 else 1 if hr <= 50 else 0 if hr <= 90 else 1 if hr <= 110 else 2 if hr <= 130 else 3
    s += 3 if a.get("new_confusion") else 0
    single_3 = any([rr <= 8 or rr >= 25, spo2 <= 91, temp <= 35.0, sbp <= 90 or sbp >= 220, hr <= 40 or hr >= 131, bool(a.get("new_confusion"))])
    if s >= 7:
        level = "high"
    elif s >= 5 or single_3:
        level = "medium"
    else:
        level = "low"
    return s, level


VULNERABILITY_FLAGS = (
    "age_under_5", "age_over_65", "pregnant", "physically_disabled", "mentally_challenged",
    "unconscious", "immunocompromised", "unaccompanied", "substance_abuse", "restrained",
)


def vulnerability(a):
    score = sum(1 for k in VULNERABILITY_FLAGS if a.get(k))
    return score, ("vulnerable" if score else "not_vulnerable")


HUMPTY_DUMPTY_POINTS = {
    # Each item scores 1 (lowest risk) up to its maximum; total 7-23.
    "age": {"under_3": 4, "3_to_6": 3, "7_to_12": 2, "13_plus": 1},
    "gender": {"male": 2, "female": 1},
    "diagnosis": {"neurological": 4, "altered_oxygenation": 3, "psych_behavioural": 2, "other": 1},
    "cognition": {"not_aware_of_limitations": 3, "forgets_limitations": 2, "oriented": 1},
    "environment": {"history_of_falls_or_infant_in_bed": 4, "assistive_device_or_crib": 3, "in_bed": 2, "outpatient": 1},
    "surgery_sedation": {"within_24h": 3, "within_48h": 2, "over_48h_or_none": 1},
    "medication": {"two_or_more": 3, "one": 2, "none": 1},
}


def humpty_dumpty(a):
    """Humpty Dumpty Falls Scale (paediatric). 7-11 low, 12+ high."""
    score = sum(opts.get(a.get(k), min(opts.values())) for k, opts in HUMPTY_DUMPTY_POINTS.items())
    return score, "high" if score >= 12 else "low"


PAIN_SCALE = [  # Numeric pain rating scale 0-10, with the words patients are shown
    (0, "No pain"), (1, "Very mild"), (2, "Minor"), (3, "Noticeable"), (4, "Moderate"), (5, "Moderately strong"),
    (6, "Moderately stronger"), (7, "Strong"), (8, "Very strong"), (9, "Very hard to bear"), (10, "Worst possible pain"),
]


def numeric_pain(a):
    """Numeric pain rating scale: 0 none, 1-3 mild, 4-6 moderate, 7-10 severe."""
    score = min(max(_i(a, "score"), 0), 10)
    return score, "none" if score == 0 else "mild" if score <= 3 else "moderate" if score <= 6 else "severe"


SCORERS = {
    "morse_fall": morse_fall,
    "humpty_dumpty": humpty_dumpty,
    "braden": braden,
    "caprini": caprini,
    "news2": news2,
    "vulnerability": vulnerability,
    "numeric_pain": numeric_pain,
}

HIGH_RISK_LEVELS = {"high", "very_high", "highest", "vulnerable", "medium", "severe"}


def score(tool, answers):
    fn = SCORERS.get(tool)
    if fn is None:
        raise ValueError(f"Unknown risk tool {tool!r}.")
    return fn(answers or {})


BARTHEL_MAX = {
    "feeding": 10, "bathing": 5, "grooming": 5, "dressing": 10, "bowels": 10, "bladder": 10,
    "toilet_use": 10, "transfers": 15, "mobility": 15, "stairs": 10,
}


def barthel_total(scores):
    return sum(min(max(_i(scores, k), 0), m) for k, m in BARTHEL_MAX.items())


# --- forms and intervention checklists ------------------------------------------
# What the screen shows for each tool, so nurses tick answers instead of
# typing JSON. Each item: {key, label, type: bool|choice|number, options?: [[value, label]]}.

def _choice(key, label, opts):
    return {"key": key, "label": label, "type": "choice", "options": [list(o) for o in opts]}


def _bool(key, label):
    return {"key": key, "label": label, "type": "bool"}


def _num(key, label, lo=None, hi=None):
    return {"key": key, "label": label, "type": "number", "min": lo, "max": hi}


_BRADEN_OPTS = {
    "sensory_perception": [(1, "Completely limited"), (2, "Very limited"), (3, "Slightly limited"), (4, "No impairment")],
    "moisture": [(1, "Constantly moist"), (2, "Very moist"), (3, "Occasionally moist"), (4, "Rarely moist")],
    "activity": [(1, "Bedfast"), (2, "Chairfast"), (3, "Walks occasionally"), (4, "Walks frequently")],
    "mobility": [(1, "Completely immobile"), (2, "Very limited"), (3, "Slightly limited"), (4, "No limitation")],
    "nutrition": [(1, "Very poor"), (2, "Probably inadequate"), (3, "Adequate"), (4, "Excellent")],
    "friction_shear": [(1, "Problem"), (2, "Potential problem"), (3, "No apparent problem")],
}

RISK_FORMS = {
    "morse_fall": [
        _bool("history_of_falling", "History of falling (immediate or within 3 months)"),
        _bool("secondary_diagnosis", "Secondary diagnosis"),
        _choice("ambulatory_aid", "Ambulatory aid", [("none", "None / bed rest / nurse assist"), ("crutches_cane_walker", "Crutches / cane / walker"), ("furniture", "Furniture")]),
        _bool("iv_or_heparin_lock", "IV therapy / heparin lock"),
        _choice("gait", "Gait", [("normal", "Normal / bed rest / wheelchair"), ("weak", "Weak"), ("impaired", "Impaired")]),
        _bool("forgets_limitations", "Overestimates or forgets limitations"),
    ],
    "humpty_dumpty": [
        _choice("age", "Age", [("under_3", "Under 3 years"), ("3_to_6", "3 to 6 years"), ("7_to_12", "7 to 12 years"), ("13_plus", "13 years and above")]),
        _choice("gender", "Gender", [("male", "Male"), ("female", "Female")]),
        _choice("diagnosis", "Diagnosis", [("neurological", "Neurological"), ("altered_oxygenation", "Altered oxygenation (respiratory, dehydration, anaemia, anorexia, syncope)"),
                                            ("psych_behavioural", "Psych / behavioural disorders"), ("other", "Other")]),
        _choice("cognition", "Cognitive impairment", [("not_aware_of_limitations", "Not aware of limitations"), ("forgets_limitations", "Forgets limitations"), ("oriented", "Oriented to own ability")]),
        _choice("environment", "Environmental factors", [("history_of_falls_or_infant_in_bed", "History of falls, or infant/toddler placed in bed"),
                                                         ("assistive_device_or_crib", "Uses assistive devices, or infant/toddler in crib"), ("in_bed", "Patient in bed"), ("outpatient", "Outpatient area")]),
        _choice("surgery_sedation", "Response to surgery / sedation / anaesthesia", [("within_24h", "Within 24 hours"), ("within_48h", "Within 48 hours"), ("over_48h_or_none", "More than 48 hours / none")]),
        _choice("medication", "Medication (sedatives, hypnotics, barbiturates, phenothiazines, antidepressants, laxatives/diuretics, narcotics)",
                [("two_or_more", "Two or more"), ("one", "One of the above"), ("none", "Other / none")]),
    ],
    "braden": [_choice(k, k.replace("_", " ").capitalize(), v) for k, v in _BRADEN_OPTS.items()],
    "caprini": [_bool(k, k.replace("_", " ").capitalize() + f" ({p})") for k, p in CAPRINI_POINTS.items()],
    "news2": [
        _num("respiratory_rate", "Respiratory rate (/min)", 0, 80), _num("spo2", "SpO2 (%)", 50, 100), _bool("on_supplemental_oxygen", "On supplemental oxygen"),
        _num("temperature_c", "Temperature (C)", 30, 44), _num("systolic_bp", "Systolic BP (mmHg)", 40, 300), _num("heart_rate", "Heart rate (/min)", 20, 250),
        _bool("new_confusion", "New confusion (CVPU)"),
    ],
    "vulnerability": [_bool(k, k.replace("_", " ").capitalize()) for k in VULNERABILITY_FLAGS],
    "numeric_pain": [
        _choice("score", "Pain score (0-10)", [(n, f"{n} - {w}") for n, w in PAIN_SCALE]),
        _choice("location", "Site", [("head", "Head / face"), ("chest", "Chest"), ("abdomen", "Abdomen"), ("back", "Back"), ("limb", "Limb / joint"), ("surgical_site", "Surgical site"), ("other", "Other")]),
        _choice("character", "Character", [("aching", "Aching"), ("sharp", "Sharp / stabbing"), ("burning", "Burning"), ("throbbing", "Throbbing"), ("colicky", "Colicky"), ("other", "Other")]),
    ],
}

FALL_HIGH = [
    "High fall risk band / bedside sign", "Bed in lowest position, brakes on", "Both side rails up",
    "Call bell and personal items within reach", "Assist with every transfer and toileting", "Non-slip footwear",
    "Hourly rounding", "Educate patient and attendant about fall risk", "Review sedating medicines with the doctor",
]
FALL_MODERATE = ["Bed in lowest position, brakes on", "Call bell within reach", "Non-slip footwear", "Assist when walking", "Educate patient and attendant"]
FALL_LOW = ["Orient to room and call bell", "Clear the floor of obstacles", "Adequate lighting at night"]

DEFAULT_INTERVENTIONS = {
    ("morse_fall", "high"): FALL_HIGH, ("morse_fall", "moderate"): FALL_MODERATE, ("morse_fall", "low"): FALL_LOW,
    ("humpty_dumpty", "high"): FALL_HIGH[:4] + ["Crib with side rails up / child never left unattended in bed", "Parent / attendant educated and present", "Hourly rounding"],
    ("humpty_dumpty", "low"): FALL_LOW + ["Side rails up when child in bed", "Parent educated about fall prevention"],
    ("braden", "very_high"): ["Turn every 2 hours (turning chart)", "Pressure-relieving mattress", "Keep skin clean and dry", "Heel off-loading",
                              "Nutrition referral", "Moisture barrier for incontinence", "Daily skin inspection documented"],
    ("braden", "high"): ["Turn every 2 hours (turning chart)", "Pressure-relieving mattress", "Keep skin clean and dry", "Heel off-loading", "Nutrition referral"],
    ("braden", "moderate"): ["Turn every 3 hours", "Pressure-redistribution cushion / mattress", "Keep skin clean and dry", "Daily skin inspection"],
    ("braden", "mild"): ["Encourage mobility", "Daily skin inspection", "Manage moisture"],
    ("caprini", "highest"): ["Pharmacological prophylaxis as prescribed", "Mechanical prophylaxis (IPC / stockings)", "Early mobilisation", "Hydration"],
    ("caprini", "high"): ["Pharmacological prophylaxis as prescribed", "Mechanical prophylaxis (IPC / stockings)", "Early mobilisation"],
    ("caprini", "moderate"): ["Mechanical prophylaxis (IPC / stockings)", "Early mobilisation"],
    ("caprini", "low"): ["Early mobilisation"],
    ("news2", "high"): ["Inform the treating doctor immediately", "Call the rapid response team", "Continuous monitoring", "Repeat NEWS2 every 15-30 min"],
    ("news2", "medium"): ["Inform the treating doctor within 30 minutes", "Repeat NEWS2 every hour", "Consider higher level of care"],
    ("numeric_pain", "severe"): ["Inform the treating doctor now", "Give prescribed analgesia (or rescue dose) as charted", "Position for comfort; splint / support the painful part",
                                 "Reassess pain within 30 minutes of analgesia", "Monitor sedation score and respiratory rate after opioids", "Document pain score every 2 hours until below 4"],
    ("numeric_pain", "moderate"): ["Give prescribed analgesia as charted", "Non-drug measures: positioning, cold / warm pack, relaxation", "Reassess pain within 1 hour of analgesia",
                                   "Inform the doctor if not settling"],
    ("numeric_pain", "mild"): ["Non-drug measures: positioning, reassurance, distraction", "Offer prescribed PRN analgesia if the patient wishes", "Reassess pain each shift"],
    ("numeric_pain", "none"): ["Reassess pain each shift and after procedures"],
    ("vulnerability", "vulnerable"): ["Bed near the nursing station", "Attendant present at all times", "Identity band checked", "Safeguarding concerns reported"],
}

LEVELS = {
    "morse_fall": ["low", "moderate", "high"], "humpty_dumpty": ["low", "high"], "braden": ["no_risk", "mild", "moderate", "high", "very_high"],
    "caprini": ["very_low", "low", "moderate", "high", "highest"], "news2": ["low", "medium", "high"], "vulnerability": ["not_vulnerable", "vulnerable"],
    "numeric_pain": ["none", "mild", "moderate", "severe"],
}
