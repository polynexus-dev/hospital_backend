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


SCORERS = {
    "morse_fall": morse_fall,
    "braden": braden,
    "caprini": caprini,
    "news2": news2,
    "vulnerability": vulnerability,
}

HIGH_RISK_LEVELS = {"high", "very_high", "highest", "vulnerable", "medium"}


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
