"""Pre-built OPD specialty consultation templates.

Each is an AssessmentTemplate: (name, category, setting, fields). Field types
are the ones AssessmentTemplateSerializer accepts (text, textarea, number,
select, multiselect, boolean, date). Only the few answers a specialist would
always record are `required`; everything else is optional so a quick
follow-up visit isn't blocked. Hospitals can edit or deactivate any of them —
`install_specialty_library` only ever adds templates whose name is missing,
it never overwrites a hospital's edits.
"""

YES_NO = ["Yes", "No"]


def _f(key, label, type_="text", options=None, required=False):
    field = {"key": key, "label": label, "type": type_}
    if options:
        field["options"] = options
    if required:
        field["required"] = True
    return field


SPECIALTY_TEMPLATES = [
    ("Cardiology consultation", "cardiology", "opd", [
        _f("presenting_symptoms", "Presenting symptoms", "multiselect", ["Chest pain", "Breathlessness", "Palpitations", "Syncope", "Pedal oedema", "Orthopnoea", "PND", "Fatigue"], required=True),
        _f("chest_pain_character", "Chest pain character / duration", "textarea"),
        _f("nyha_class", "NYHA functional class", "select", ["I", "II", "III", "IV"]),
        _f("ccs_angina_class", "CCS angina class", "select", ["0 – none", "I", "II", "III", "IV"]),
        _f("risk_factors", "Risk factors", "multiselect", ["Hypertension", "Diabetes", "Dyslipidaemia", "Smoking", "Family history of premature CAD", "Obesity", "CKD"]),
        _f("jvp", "JVP", "select", ["Not raised", "Raised"]),
        _f("heart_sounds", "Heart sounds / murmurs", "text"),
        _f("ecg_findings", "ECG findings", "textarea"),
        _f("echo_lvef", "Echo LVEF (%)", "number"),
        _f("previous_interventions", "Previous PCI / CABG / devices", "text"),
    ]),
    ("Orthopaedics consultation", "orthopaedics", "opd", [
        _f("complaint_region", "Region", "select", ["Cervical spine", "Lumbar spine", "Shoulder", "Elbow", "Wrist / hand", "Hip", "Knee", "Ankle / foot", "Multiple joints"], required=True),
        _f("side", "Side", "select", ["Left", "Right", "Bilateral", "Not applicable"]),
        _f("mechanism", "Onset / mechanism of injury", "select", ["Insidious", "Trauma – fall", "Trauma – road traffic accident", "Sports injury", "Post-operative"]),
        _f("pain_vas", "Pain score (VAS 0–10)", "number"),
        _f("swelling_deformity", "Swelling / deformity", "text"),
        _f("range_of_motion", "Range of motion", "textarea"),
        _f("neurovascular_status", "Distal neurovascular status", "select", ["Intact", "Impaired"]),
        _f("special_tests", "Special tests (e.g. Lachman, McMurray, SLR)", "textarea"),
        _f("imaging", "X-ray / MRI findings", "textarea"),
        _f("plan", "Plan", "multiselect", ["Conservative", "Physiotherapy", "Immobilisation / cast", "Injection", "Surgery advised"]),
    ]),
    ("Gynaecology consultation", "gynaecology", "opd", [
        _f("lmp", "LMP", "date"),
        _f("cycle_pattern", "Menstrual cycle", "select", ["Regular", "Irregular", "Menopausal", "Amenorrhoea"], required=True),
        _f("cycle_details", "Cycle length / duration / flow", "text"),
        _f("obstetric_history", "Obstetric history (G P L A)", "text"),
        _f("complaints", "Complaints", "multiselect", ["Heavy menstrual bleeding", "Dysmenorrhoea", "White discharge", "Pelvic pain", "Infertility", "Post-menopausal bleeding", "Mass per abdomen", "Urinary symptoms"]),
        _f("contraception", "Contraception", "text"),
        _f("breast_exam", "Breast examination", "text"),
        _f("per_speculum", "Per speculum", "textarea"),
        _f("per_vaginal", "Per vaginal", "textarea"),
        _f("last_pap_smear", "Last Pap smear / HPV test", "date"),
        _f("usg_findings", "USG findings", "textarea"),
    ]),
    ("Dermatology consultation", "dermatology", "opd", [
        _f("duration", "Duration of lesions", "text", required=True),
        _f("symptoms", "Symptoms", "multiselect", ["Itching", "Pain / burning", "Scaling", "Discharge", "Hair loss", "Nail changes", "Photosensitivity"]),
        _f("morphology", "Primary lesion morphology", "multiselect", ["Macule", "Patch", "Papule", "Plaque", "Nodule", "Vesicle", "Bulla", "Pustule", "Wheal", "Ulcer"]),
        _f("distribution", "Distribution / sites", "text"),
        _f("bsa_percent", "Body surface area involved (%)", "number"),
        _f("pasi_score", "PASI (psoriasis)", "number"),
        _f("koebner_or_nikolsky", "Koebner / Nikolsky sign", "text"),
        _f("previous_treatment", "Previous treatment incl. topical steroids", "textarea"),
        _f("photo_taken", "Clinical photograph taken (with consent)", "boolean"),
    ]),
    ("Neurology consultation", "neurology", "opd", [
        _f("presenting_complaint", "Presenting complaint", "multiselect", ["Headache", "Seizure", "Weakness", "Numbness", "Giddiness / vertigo", "Tremor", "Memory loss", "Speech disturbance", "Gait disturbance"], required=True),
        _f("onset", "Onset", "select", ["Sudden", "Acute (days)", "Subacute (weeks)", "Chronic"]),
        _f("gcs", "GCS (3–15)", "number"),
        _f("higher_functions", "Higher mental functions", "textarea"),
        _f("cranial_nerves", "Cranial nerves", "textarea"),
        _f("motor_power", "Motor power (MRC grade by limb)", "text"),
        _f("reflexes", "Reflexes / plantars", "text"),
        _f("sensory", "Sensory examination", "text"),
        _f("cerebellar_gait", "Cerebellar signs / gait", "text"),
        _f("seizure_frequency", "Seizures in last month", "number"),
        _f("nihss", "NIHSS (stroke)", "number"),
    ]),
    ("Gastroenterology consultation", "gastroenterology", "opd", [
        _f("symptoms", "Symptoms", "multiselect", ["Abdominal pain", "Dyspepsia", "Vomiting", "Dysphagia", "Diarrhoea", "Constipation", "GI bleed", "Jaundice", "Abdominal distension", "Weight loss"], required=True),
        _f("pain_site", "Site / character of pain", "text"),
        _f("bowel_habits", "Bowel habits", "text"),
        _f("alcohol_intake", "Alcohol intake", "select", ["None", "Occasional", "Regular", "Heavy"]),
        _f("abdominal_exam", "Abdominal examination", "textarea"),
        _f("liver_spleen", "Liver / spleen", "text"),
        _f("ascites", "Ascites", "select", ["Absent", "Mild", "Moderate / tense"]),
        _f("child_pugh", "Child-Pugh class (if cirrhosis)", "select", ["Not applicable", "A", "B", "C"]),
        _f("endoscopy_findings", "Previous endoscopy / colonoscopy", "textarea"),
    ]),
    ("Pulmonology consultation", "pulmonology", "opd", [
        _f("symptoms", "Symptoms", "multiselect", ["Cough", "Sputum", "Haemoptysis", "Breathlessness", "Wheeze", "Chest pain", "Fever", "Snoring / daytime sleepiness"], required=True),
        _f("duration", "Duration", "text"),
        _f("mmrc_dyspnoea", "mMRC dyspnoea grade", "select", ["0", "1", "2", "3", "4"]),
        _f("smoking_pack_years", "Smoking (pack-years)", "number"),
        _f("occupational_exposure", "Occupational / biomass exposure", "text"),
        _f("tb_history", "Past tuberculosis / contact", "text"),
        _f("spo2_room_air", "SpO₂ on room air (%)", "number"),
        _f("auscultation", "Auscultation", "textarea"),
        _f("spirometry", "Spirometry (FEV1, FVC, FEV1/FVC)", "text"),
        _f("cat_score", "COPD Assessment Test score", "number"),
        _f("imaging", "Chest X-ray / CT findings", "textarea"),
    ]),
    ("Nephrology consultation", "nephrology", "opd", [
        _f("indication", "Reason for referral", "multiselect", ["Raised creatinine", "Proteinuria", "Haematuria", "Oedema", "Hypertension", "Electrolyte disturbance", "Dialysis follow-up", "Transplant follow-up"], required=True),
        _f("creatinine", "Serum creatinine (mg/dL)", "number"),
        _f("egfr", "eGFR (mL/min/1.73m²)", "number"),
        _f("ckd_stage", "CKD stage", "select", ["Not CKD", "G1", "G2", "G3a", "G3b", "G4", "G5", "G5D (on dialysis)"]),
        _f("uacr", "Urine albumin–creatinine ratio (mg/g)", "number"),
        _f("urine_output", "Urine output", "text"),
        _f("oedema", "Oedema", "select", ["None", "Pedal", "Generalised"]),
        _f("dialysis_access", "Dialysis access", "select", ["Not applicable", "AV fistula", "AV graft", "Tunnelled catheter", "Temporary catheter", "Peritoneal"]),
        _f("dry_weight_kg", "Dry weight (kg)", "number"),
        _f("nephrotoxic_drugs", "Nephrotoxic drugs reviewed / stopped", "boolean"),
    ]),
    ("Urology consultation", "urology", "opd", [
        _f("symptoms", "Symptoms", "multiselect", ["Frequency", "Urgency", "Nocturia", "Poor stream", "Hesitancy", "Dysuria", "Haematuria", "Retention", "Flank pain", "Scrotal swelling"], required=True),
        _f("ipss", "IPSS score (0–35)", "number"),
        _f("ipss_qol", "IPSS quality-of-life score (0–6)", "number"),
        _f("dre", "Digital rectal examination / prostate", "text"),
        _f("psa", "PSA (ng/mL)", "number"),
        _f("pvr_ml", "Post-void residual (mL)", "number"),
        _f("uroflowmetry_qmax", "Uroflowmetry Qmax (mL/s)", "number"),
        _f("stone_details", "Stone size / site (imaging)", "text"),
        _f("genital_exam", "Genital examination", "text"),
    ]),
    ("Endocrinology / Diabetes consultation", "endocrinology", "opd", [
        _f("diagnosis_type", "Condition", "select", ["Type 2 diabetes", "Type 1 diabetes", "Prediabetes", "Gestational diabetes", "Thyroid disorder", "Obesity", "PCOS", "Other endocrine"], required=True),
        _f("duration_years", "Duration (years)", "number"),
        _f("hba1c", "HbA1c (%)", "number"),
        _f("fbs", "Fasting glucose (mg/dL)", "number"),
        _f("ppbs", "Post-prandial glucose (mg/dL)", "number"),
        _f("hypoglycaemia_episodes", "Hypoglycaemia episodes since last visit", "number"),
        _f("bmi", "BMI (kg/m²)", "number"),
        _f("foot_exam", "Foot examination (monofilament, pulses, ulcers)", "select", ["Normal", "Loss of protective sensation", "Ulcer present", "Not done"]),
        _f("retinopathy_screen", "Retinopathy screening done this year", "boolean"),
        _f("tsh", "TSH (mIU/L)", "number"),
        _f("complications", "Complications", "multiselect", ["Neuropathy", "Nephropathy", "Retinopathy", "CAD", "Stroke", "PAD"]),
    ]),
    ("Psychiatry consultation", "psychiatry", "opd", [
        _f("presenting_concern", "Presenting concern", "textarea", required=True),
        _f("mse_appearance_behaviour", "MSE – appearance & behaviour", "text"),
        _f("mse_mood_affect", "MSE – mood & affect", "text"),
        _f("mse_thought", "MSE – thought form & content", "textarea"),
        _f("mse_perception", "MSE – perception", "text"),
        _f("insight", "Insight", "select", ["Present", "Partial", "Absent"]),
        _f("phq9", "PHQ-9 score (0–27)", "number"),
        _f("gad7", "GAD-7 score (0–21)", "number"),
        _f("suicide_risk", "Suicide risk", "select", ["Low", "Moderate", "High"], required=True),
        _f("substance_use", "Substance use", "multiselect", ["Alcohol", "Tobacco", "Cannabis", "Opioids", "Sedatives", "None"]),
        _f("mhca_status", "Mental Healthcare Act 2017 admission status (if admitted)", "select", ["Not applicable", "Independent", "Supported"]),
    ]),
    ("General surgery consultation", "surgery", "opd", [
        _f("presenting_complaint", "Presenting complaint", "select", ["Lump / swelling", "Hernia", "Abdominal pain", "Wound / ulcer", "Anorectal complaint", "Breast complaint", "Thyroid swelling", "Varicose veins", "Other"], required=True),
        _f("swelling_characteristics", "Swelling – site, size, consistency, mobility", "textarea"),
        _f("cough_impulse", "Cough impulse / reducibility (hernia)", "text"),
        _f("abdominal_exam", "Abdominal examination", "textarea"),
        _f("per_rectal", "Per rectal / proctoscopy", "text"),
        _f("asa_grade", "ASA grade (if surgery planned)", "select", ["I", "II", "III", "IV", "V"]),
        _f("surgery_advised", "Surgery advised", "text"),
        _f("investigations_ordered", "Investigations ordered", "textarea"),
    ]),
    ("Dental consultation", "dental", "opd", [
        _f("chief_complaint_teeth", "Teeth involved (FDI notation, e.g. 36, 46)", "text", required=True),
        _f("complaint_type", "Complaint", "multiselect", ["Toothache", "Sensitivity", "Bleeding gums", "Swelling", "Mobility", "Missing teeth", "Cosmetic", "Trauma"]),
        _f("caries", "Caries (teeth)", "text"),
        _f("periodontal_status", "Periodontal status", "select", ["Healthy", "Gingivitis", "Mild periodontitis", "Moderate periodontitis", "Severe periodontitis"]),
        _f("oral_hygiene", "Oral hygiene", "select", ["Good", "Fair", "Poor"]),
        _f("soft_tissue", "Soft tissue / oral mucosa", "text"),
        _f("xray", "IOPA / OPG findings", "textarea"),
        _f("treatment_plan", "Treatment plan", "multiselect", ["Scaling", "Restoration", "Root canal", "Extraction", "Crown / bridge", "Implant", "Orthodontic referral"]),
    ]),
    ("Physiotherapy assessment", "rehab", "opd", [
        _f("referral_diagnosis", "Referral diagnosis", "text", required=True),
        _f("pain_vas", "Pain score (VAS 0–10)", "number"),
        _f("range_of_motion", "Range of motion (goniometry)", "textarea"),
        _f("muscle_power", "Muscle power (MRC grade)", "text"),
        _f("balance_gait", "Balance / gait", "text"),
        _f("functional_scale", "Functional score (e.g. Barthel, ODI, WOMAC)", "text"),
        _f("goals", "Short- and long-term goals", "textarea"),
        _f("modalities", "Treatment", "multiselect", ["Exercise therapy", "Manual therapy", "TENS / IFT", "Ultrasound", "Hot / cold packs", "Traction", "Gait training"]),
        _f("sessions_planned", "Sessions planned", "number"),
    ]),
]


def install_specialty_library(hospital, get_model=None):
    """Adds every library template this hospital doesn't have yet (matched by
    name). Returns the names added. Safe to run repeatedly."""
    if get_model is None:
        from django.apps import apps as django_apps

        get_model = django_apps.get_model
    AssessmentTemplate = get_model("clinical", "AssessmentTemplate")
    have = set(AssessmentTemplate.objects.filter(hospital=hospital).values_list("name", flat=True))
    new = [t for t in SPECIALTY_TEMPLATES if t[0] not in have]
    AssessmentTemplate.objects.bulk_create([
        AssessmentTemplate(hospital=hospital, name=n, category=c, setting=s, fields=f) for n, c, s, f in new
    ])
    return [t[0] for t in new]
