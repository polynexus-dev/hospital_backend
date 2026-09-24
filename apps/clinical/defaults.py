"""
Starter clinical knowledge seeded into every hospital (new ones via the
post_save signal in apps.clinical.signals, existing ones via migration
0002). Hospitals can edit or extend all of it — it's a sensible,
conservative baseline, not a substitute for the hospital's own Drugs &
Therapeutics / Infection Control committee decisions.
"""

INTERACTIONS = [
    ("warfarin", "aspirin", "major", "Additive bleeding risk.", "Avoid unless specifically indicated; monitor INR and for bleeding."),
    ("warfarin", "nsaid", "major", "NSAIDs increase bleeding risk and GI bleeding with warfarin.", "Prefer paracetamol for analgesia."),
    ("warfarin", "fluconazole", "major", "Fluconazole inhibits warfarin metabolism — INR rises sharply.", "Reduce warfarin dose and monitor INR closely."),
    ("simvastatin", "clarithromycin", "contraindicated", "Strong CYP3A4 inhibition — risk of rhabdomyolysis.", "Withhold simvastatin during the macrolide course."),
    ("simvastatin", "amlodipine", "moderate", "Amlodipine raises simvastatin levels.", "Do not exceed simvastatin 20 mg/day."),
    ("clopidogrel", "omeprazole", "moderate", "Omeprazole reduces clopidogrel activation.", "Prefer pantoprazole."),
    ("spironolactone", "enalapril", "major", "Risk of hyperkalaemia.", "Monitor potassium and renal function."),
    ("spironolactone", "ramipril", "major", "Risk of hyperkalaemia.", "Monitor potassium and renal function."),
    ("sildenafil", "nitrate", "contraindicated", "Severe, potentially fatal hypotension.", "Do not co-prescribe."),
    ("tramadol", "sertraline", "major", "Serotonin syndrome and seizure risk.", "Avoid or monitor closely."),
    ("methotrexate", "trimethoprim", "major", "Additive antifolate effect — bone-marrow suppression.", "Avoid combination."),
    ("digoxin", "amiodarone", "major", "Amiodarone raises digoxin levels.", "Halve digoxin dose and monitor levels."),
    ("metformin", "iodinated contrast", "major", "Risk of lactic acidosis with contrast-induced nephropathy.", "Withhold metformin at time of and 48h after contrast; check eGFR."),
    ("ciprofloxacin", "theophylline", "major", "Raised theophylline levels — seizures.", "Monitor levels or choose another antibiotic."),
    ("linezolid", "sertraline", "major", "Serotonin syndrome.", "Avoid combination."),
]

CONDITION_RULES = [
    ("metformin", ["n18", "chronic kidney disease", "ckd", "renal failure"], "major", "Metformin is contraindicated when eGFR < 30 — check renal function.", "medication"),
    ("nsaid", ["n18", "ckd", "chronic kidney disease", "peptic ulcer", "k25", "k27"], "major", "NSAIDs can worsen renal function / cause GI bleeding.", "medication"),
    ("nsaid", ["pregnant"], "major", "Avoid NSAIDs in pregnancy (especially third trimester).", "medication"),
    ("atenolol", ["j45", "asthma"], "major", "Beta-blockers can precipitate bronchospasm in asthma.", "medication"),
    ("propranolol", ["j45", "asthma"], "contraindicated", "Non-selective beta-blocker — contraindicated in asthma.", "medication"),
    ("warfarin", ["pregnant"], "contraindicated", "Warfarin is teratogenic.", "medication"),
    ("isotretinoin", ["pregnant"], "contraindicated", "Isotretinoin is teratogenic.", "medication"),
    ("ceftriaxone", ["child"], "moderate", "Check weight-based dosing in children.", "medication"),
    ("contrast", ["n18", "ckd", "renal failure", "chronic kidney disease"], "major", "Iodinated contrast risks contrast-induced nephropathy — check eGFR, hydrate, consider non-contrast study.", "radiology"),
    ("ct", ["pregnant"], "major", "Ionising radiation in pregnancy — confirm necessity, shield, or consider USG/MRI.", "radiology"),
    ("xray", ["pregnant"], "moderate", "Ionising radiation in pregnancy — confirm necessity and shield.", "radiology"),
    ("mri", ["pacemaker", "cochlear implant", "metallic implant", "aneurysm clip"], "contraindicated", "MRI with a non-MR-conditional implant is contraindicated.", "radiology"),
]

NOTIFIABLE = [
    ("Dengue", ["A90", "A91", "A97"], ["dengue"]),
    ("Malaria", ["B50", "B51", "B52", "B53", "B54"], ["malaria"]),
    ("Cholera", ["A00"], ["cholera"]),
    ("Typhoid / enteric fever", ["A01"], ["typhoid", "enteric fever"]),
    ("Tuberculosis", ["A15", "A16", "A17", "A18", "A19"], ["tuberculosis", "koch"]),
    ("Measles", ["B05"], ["measles"]),
    ("Diphtheria", ["A36"], ["diphtheria"]),
    ("Acute flaccid paralysis / Polio", ["A80", "G83.9"], ["acute flaccid paralysis", "poliomyelitis"]),
    ("Chikungunya", ["A92.0"], ["chikungunya"]),
    ("Japanese encephalitis", ["A83.0"], ["japanese encephalitis"]),
    ("Leptospirosis", ["A27"], ["leptospirosis"]),
    ("Viral hepatitis", ["B15", "B16", "B17", "B19"], ["hepatitis a", "hepatitis e", "viral hepatitis"]),
    ("Rabies / animal bite", ["A82", "W54"], ["rabies"]),
    ("COVID-19", ["U07.1"], ["covid", "sars-cov-2"]),
    ("Scrub typhus", ["A75.3"], ["scrub typhus"]),
    ("Leprosy", ["A30"], ["leprosy", "hansen"]),
]

TEMPLATES = [
    ("General OPD initial assessment", "general", "opd", [
        {"key": "presenting_illness", "label": "History of presenting illness", "type": "textarea", "required": True},
        {"key": "past_history", "label": "Past medical / surgical history", "type": "textarea"},
        {"key": "drug_history", "label": "Current medications", "type": "textarea"},
        {"key": "family_history", "label": "Family history", "type": "textarea"},
        {"key": "pain_score", "label": "Pain score (0–10)", "type": "number"},
        {"key": "nutritional_screen", "label": "Nutritional risk", "type": "select", "options": ["Not at risk", "At risk", "Malnourished"]},
    ]),
    ("Antenatal assessment", "antenatal", "both", [
        {"key": "lmp", "label": "LMP", "type": "date", "required": True},
        {"key": "edd", "label": "EDD", "type": "date"},
        {"key": "gravida_para", "label": "Gravida / Para / Living / Abortion", "type": "text", "required": True},
        {"key": "gestational_age_weeks", "label": "Gestational age (weeks)", "type": "number"},
        {"key": "fundal_height_cm", "label": "Fundal height (cm)", "type": "number"},
        {"key": "fetal_heart_rate", "label": "Fetal heart rate", "type": "number"},
        {"key": "high_risk_factors", "label": "High-risk factors", "type": "multiselect", "options": ["PIH", "GDM", "Anaemia", "Previous LSCS", "Twins", "Rh negative", "None"]},
    ]),
    ("Paediatric assessment", "paediatrics", "both", [
        {"key": "birth_history", "label": "Birth history", "type": "textarea"},
        {"key": "immunisation_up_to_date", "label": "Immunisation up to date", "type": "boolean"},
        {"key": "weight_for_age", "label": "Weight-for-age (z-score/percentile)", "type": "text"},
        {"key": "developmental_milestones", "label": "Developmental milestones", "type": "select", "options": ["Age appropriate", "Delayed"]},
    ]),
    ("Ophthalmology assessment", "ophthalmology", "opd", [
        {"key": "va_right", "label": "Visual acuity — right", "type": "text", "required": True},
        {"key": "va_left", "label": "Visual acuity — left", "type": "text", "required": True},
        {"key": "iop_right", "label": "IOP right (mmHg)", "type": "number"},
        {"key": "iop_left", "label": "IOP left (mmHg)", "type": "number"},
        {"key": "anterior_segment", "label": "Anterior segment", "type": "textarea"},
        {"key": "fundus", "label": "Fundus", "type": "textarea"},
    ]),
    ("ENT assessment", "ent", "opd", [
        {"key": "ear", "label": "Ear examination", "type": "textarea"},
        {"key": "nose", "label": "Nose / PNS", "type": "textarea"},
        {"key": "throat", "label": "Throat", "type": "textarea"},
        {"key": "hearing", "label": "Hearing (tuning fork / audiometry)", "type": "text"},
    ]),
    ("IPD nursing admission assessment", "nursing", "ipd", [
        {"key": "consciousness", "label": "Level of consciousness", "type": "select", "options": ["Alert", "Voice", "Pain", "Unresponsive"], "required": True},
        {"key": "mobility", "label": "Mobility", "type": "select", "options": ["Independent", "Assisted", "Bedridden"]},
        {"key": "skin_integrity", "label": "Skin integrity", "type": "select", "options": ["Intact", "Pressure injury present", "Wound"]},
        {"key": "lines_devices", "label": "Lines / catheters / devices", "type": "multiselect", "options": ["Peripheral IV", "Central line", "Urinary catheter", "Ryle's tube", "Ventilator", "None"]},
        {"key": "patient_belongings", "label": "Patient belongings recorded", "type": "boolean"},
    ]),
    ("Nutritional screening (dietary)", "dietary", "both", [
        {"key": "bmi", "label": "BMI", "type": "number", "required": True},
        {"key": "weight_loss_3m", "label": "Unintended weight loss in 3 months", "type": "select", "options": ["None", "<5%", "5–10%", ">10%"]},
        {"key": "reduced_intake", "label": "Reduced dietary intake last week", "type": "boolean"},
        {"key": "diet_preference", "label": "Diet preference", "type": "select", "options": ["Vegetarian", "Non-vegetarian", "Eggetarian", "Jain", "Vegan"]},
        {"key": "food_allergies", "label": "Food allergies / intolerances", "type": "text"},
    ]),
    ("Oncology initial assessment", "oncology", "both", [
        {"key": "primary_site", "label": "Primary site", "type": "text", "required": True},
        {"key": "ecog", "label": "ECOG performance status", "type": "select", "options": ["0", "1", "2", "3", "4"], "required": True},
        {"key": "histology", "label": "Histology", "type": "text"},
        {"key": "previous_treatment", "label": "Previous treatment", "type": "multiselect", "options": ["Surgery", "Chemotherapy", "Radiotherapy", "Immunotherapy", "None"]},
    ]),
]

ORDER_SETS = [
    ("CKD work-up", "lab", ["N18"], ["kidney", "ckd", "renal"], [
        {"type": "lab", "name": "Serum creatinine"}, {"type": "lab", "name": "Blood urea"}, {"type": "lab", "name": "Serum electrolytes"},
        {"type": "lab", "name": "Urine routine & microscopy"}, {"type": "lab", "name": "Urine albumin-creatinine ratio"},
        {"type": "radiology", "name": "USG KUB"},
    ]),
    ("Diabetes review", "lab", ["E11", "E10"], ["diabetes", "dm"], [
        {"type": "lab", "name": "HbA1c"}, {"type": "lab", "name": "Fasting blood sugar"}, {"type": "lab", "name": "Lipid profile"},
        {"type": "lab", "name": "Serum creatinine"}, {"type": "lab", "name": "Urine microalbumin"},
    ]),
    ("Fever work-up", "lab", ["R50", "A90", "B54"], ["fever", "pyrexia"], [
        {"type": "lab", "name": "CBC"}, {"type": "lab", "name": "Dengue NS1 / IgM"}, {"type": "lab", "name": "Malaria antigen"},
        {"type": "lab", "name": "Widal / typhoid IgM"}, {"type": "lab", "name": "Urine routine"},
    ]),
    ("Chest pain (ACS rule-out)", "mixed", ["I20", "I21", "R07"], ["chest pain", "acs"], [
        {"type": "lab", "name": "Troponin I"}, {"type": "lab", "name": "CBC"}, {"type": "lab", "name": "Serum electrolytes"},
        {"type": "radiology", "name": "Chest X-ray PA"}, {"type": "medication", "name": "Aspirin", "dose": "300 mg", "frequency": "STAT", "route": "Oral"},
    ]),
]

HOMECARE = [
    ("Home blood sample collection", "sample_collection", 200),
    ("Home nursing visit", "nursing", 800),
    ("Home physiotherapy session", "physiotherapy", 700),
    ("Doctor home visit", "doctor_visit", 1500),
]


def seed_hospital(hospital, get_model=None):
    """`get_model` lets a data migration pass its historical-model getter."""
    if get_model is None:
        from django.apps import apps as django_apps

        get_model = django_apps.get_model
    AssessmentTemplate = get_model("clinical", "AssessmentTemplate")
    DrugConditionRule = get_model("clinical", "DrugConditionRule")
    DrugInteraction = get_model("clinical", "DrugInteraction")
    HomecareService = get_model("clinical", "HomecareService")
    NotifiableDisease = get_model("clinical", "NotifiableDisease")
    OrderSet = get_model("clinical", "OrderSet")

    if not DrugInteraction.objects.filter(hospital=hospital).exists():
        DrugInteraction.objects.bulk_create([
            DrugInteraction(hospital=hospital, drug_a=a, drug_b=b, severity=sev, description=d, recommendation=r) for a, b, sev, d, r in INTERACTIONS
        ])
    if not DrugConditionRule.objects.filter(hospital=hospital).exists():
        DrugConditionRule.objects.bulk_create([
            DrugConditionRule(hospital=hospital, drug=drug, condition_keywords=kw, severity=sev, message=msg, applies_to=to) for drug, kw, sev, msg, to in CONDITION_RULES
        ])
    if not NotifiableDisease.objects.filter(hospital=hospital).exists():
        NotifiableDisease.objects.bulk_create([
            NotifiableDisease(hospital=hospital, name=n, icd_codes=codes, keywords=kw) for n, codes, kw in NOTIFIABLE
        ])
    if not AssessmentTemplate.objects.filter(hospital=hospital).exists():
        AssessmentTemplate.objects.bulk_create([
            AssessmentTemplate(hospital=hospital, name=n, category=c, setting=s, fields=f) for n, c, s, f in TEMPLATES
        ])
    if not OrderSet.objects.filter(hospital=hospital).exists():
        OrderSet.objects.bulk_create([
            OrderSet(hospital=hospital, name=n, kind=k, diagnosis_codes=codes, diagnosis_keywords=kw, items=items) for n, k, codes, kw, items in ORDER_SETS
        ])
    if not HomecareService.objects.filter(hospital=hospital).exists():
        HomecareService.objects.bulk_create([HomecareService(hospital=hospital, name=n, kind=k, price=p) for n, k, p in HOMECARE])
