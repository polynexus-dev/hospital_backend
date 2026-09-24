"""ICU severity scores & predicted mortality (NABH COP.5.b; ICU SMR KPI).

APACHE II: Knaus et al. 1985 physiology table + age + chronic health;
predicted mortality via the published logistic equation
ln(R/(1-R)) = -3.517 + 0.146·APACHE + 0.603·(emergency surgery)
+ diagnostic-category weight (defaults to 0 when the category isn't given).

SOFA: Vincent et al. 1996; mortality bands from Ferreira et al. 2001.
"""
import math


def _band(value, bands, default=0):
    """bands: [(predicate, points)] evaluated in order."""
    if value is None:
        return default
    for pred, pts in bands:
        if pred(value):
            return pts
    return default


def _f(d, k):
    v = d.get(k)
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def apache_ii(d):
    temp = _band(_f(d, "temperature_c"), [(lambda v: v >= 41, 4), (lambda v: v >= 39, 3), (lambda v: v >= 38.5, 1), (lambda v: v >= 36, 0), (lambda v: v >= 34, 1), (lambda v: v >= 32, 2), (lambda v: v >= 30, 3), (lambda v: True, 4)])
    mapv = _band(_f(d, "mean_arterial_pressure"), [(lambda v: v >= 160, 4), (lambda v: v >= 130, 3), (lambda v: v >= 110, 2), (lambda v: v >= 70, 0), (lambda v: v >= 50, 2), (lambda v: True, 4)])
    hr = _band(_f(d, "heart_rate"), [(lambda v: v >= 180, 4), (lambda v: v >= 140, 3), (lambda v: v >= 110, 2), (lambda v: v >= 70, 0), (lambda v: v >= 55, 2), (lambda v: v >= 40, 3), (lambda v: True, 4)])
    rr = _band(_f(d, "respiratory_rate"), [(lambda v: v >= 50, 4), (lambda v: v >= 35, 3), (lambda v: v >= 25, 1), (lambda v: v >= 12, 0), (lambda v: v >= 10, 1), (lambda v: v >= 6, 2), (lambda v: True, 4)])
    fio2 = _f(d, "fio2")
    if fio2 is not None and fio2 >= 0.5:
        oxy = _band(_f(d, "a_a_gradient"), [(lambda v: v >= 500, 4), (lambda v: v >= 350, 3), (lambda v: v >= 200, 2), (lambda v: True, 0)])
    else:
        oxy = _band(_f(d, "pao2"), [(lambda v: v > 70, 0), (lambda v: v >= 61, 1), (lambda v: v >= 55, 3), (lambda v: True, 4)])
    ph = _band(_f(d, "arterial_ph"), [(lambda v: v >= 7.7, 4), (lambda v: v >= 7.6, 3), (lambda v: v >= 7.5, 1), (lambda v: v >= 7.33, 0), (lambda v: v >= 7.25, 2), (lambda v: v >= 7.15, 3), (lambda v: True, 4)])
    na = _band(_f(d, "sodium"), [(lambda v: v >= 180, 4), (lambda v: v >= 160, 3), (lambda v: v >= 155, 2), (lambda v: v >= 150, 1), (lambda v: v >= 130, 0), (lambda v: v >= 120, 2), (lambda v: v >= 111, 3), (lambda v: True, 4)])
    k = _band(_f(d, "potassium"), [(lambda v: v >= 7, 4), (lambda v: v >= 6, 3), (lambda v: v >= 5.5, 1), (lambda v: v >= 3.5, 0), (lambda v: v >= 3, 1), (lambda v: v >= 2.5, 2), (lambda v: True, 4)])
    cr = _band(_f(d, "creatinine_mg_dl"), [(lambda v: v >= 3.5, 4), (lambda v: v >= 2, 3), (lambda v: v >= 1.5, 2), (lambda v: v >= 0.6, 0), (lambda v: True, 2)])
    if d.get("acute_renal_failure"):
        cr *= 2
    hct = _band(_f(d, "hematocrit"), [(lambda v: v >= 60, 4), (lambda v: v >= 50, 2), (lambda v: v >= 46, 1), (lambda v: v >= 30, 0), (lambda v: v >= 20, 2), (lambda v: True, 4)])
    wbc = _band(_f(d, "wbc_thousands"), [(lambda v: v >= 40, 4), (lambda v: v >= 20, 2), (lambda v: v >= 15, 1), (lambda v: v >= 3, 0), (lambda v: v >= 1, 2), (lambda v: True, 4)])
    gcs = _f(d, "gcs")
    gcs_pts = int(15 - gcs) if gcs is not None else 0
    age = _band(_f(d, "age"), [(lambda v: v >= 75, 6), (lambda v: v >= 65, 5), (lambda v: v >= 55, 3), (lambda v: v >= 45, 2), (lambda v: True, 0)])
    chronic = 0
    if d.get("severe_chronic_organ_insufficiency"):
        chronic = 2 if d.get("admission_type") == "elective_postop" else 5
    score = temp + mapv + hr + rr + oxy + ph + na + k + cr + hct + wbc + gcs_pts + age + chronic
    logit = -3.517 + 0.146 * score + (0.603 if d.get("admission_type") == "emergency_postop" else 0) + float(d.get("diagnostic_weight") or 0)
    mortality = 100 / (1 + math.exp(-logit))
    return score, round(mortality, 1)


def sofa(d):
    pf = _f(d, "pao2_fio2")
    supported = bool(d.get("respiratory_support"))
    resp = _band(pf, [(lambda v: v < 100 and supported, 4), (lambda v: v < 200 and supported, 3), (lambda v: v < 300, 2), (lambda v: v < 400, 1), (lambda v: True, 0)])
    coag = _band(_f(d, "platelets_thousands"), [(lambda v: v < 20, 4), (lambda v: v < 50, 3), (lambda v: v < 100, 2), (lambda v: v < 150, 1), (lambda v: True, 0)])
    liver = _band(_f(d, "bilirubin_mg_dl"), [(lambda v: v >= 12, 4), (lambda v: v >= 6, 3), (lambda v: v >= 2, 2), (lambda v: v >= 1.2, 1), (lambda v: True, 0)])
    vaso = d.get("vasopressor", "none")  # none | dopamine_low_or_dobutamine | dopamine_mid_or_norepi_low | dopamine_high_or_norepi_high
    cvs = {"dopamine_low_or_dobutamine": 2, "dopamine_mid_or_norepi_low": 3, "dopamine_high_or_norepi_high": 4}.get(vaso)
    if cvs is None:
        m = _f(d, "mean_arterial_pressure")
        cvs = 1 if m is not None and m < 70 else 0
    cns = _band(_f(d, "gcs"), [(lambda v: v < 6, 4), (lambda v: v < 10, 3), (lambda v: v < 13, 2), (lambda v: v < 15, 1), (lambda v: True, 0)])
    uo = _f(d, "urine_output_ml_day")
    renal = _band(_f(d, "creatinine_mg_dl"), [(lambda v: v >= 5, 4), (lambda v: v >= 3.5, 3), (lambda v: v >= 2, 2), (lambda v: v >= 1.2, 1), (lambda v: True, 0)])
    if uo is not None:
        renal = max(renal, 4 if uo < 200 else 3 if uo < 500 else 0)
    score = resp + coag + liver + cvs + cns + renal
    mortality = 5 if score <= 6 else 20 if score <= 9 else 45 if score <= 12 else 55 if score <= 14 else 85
    return score, float(mortality)


SCALES = {"apache_ii": apache_ii, "sofa": sofa}


def compute(scale, data):
    fn = SCALES.get(scale)
    if fn is None:
        raise ValueError(f"Unsupported scale {scale!r} (supported: {', '.join(SCALES)}).")
    return fn(data or {})
