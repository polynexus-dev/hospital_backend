"""Past medical / surgical / family history, habits and allergies for a share
of the demo patients — the history cards above every clinical screen."""
from apps.clinical.models import Allergy, PatientHistory

from .common import RNG, TODAY, chance, days_ago

CONDITIONS = [("Type 2 diabetes mellitus", 2016), ("Essential hypertension", 2018), ("Hypothyroidism", 2019), ("Bronchial asthma", 2010),
              ("Dyslipidaemia", 2021), ("Osteoarthritis, both knees", 2020), ("Chronic kidney disease stage 2", 2022), ("GERD", 2023),
              ("Migraine", 2015), ("Iron-deficiency anaemia", 2024)]
SURGERIES = [("Appendicectomy", 2009), ("Laparoscopic cholecystectomy", 2017), ("Caesarean section", 2014), ("Cataract surgery, right eye", 2022),
             ("Inguinal hernia repair", 2019), ("Tonsillectomy", 2001), ("PTCA with stent to LAD", 2021), ("Total knee replacement, left", 2023)]
FAMILY = [("Father", "Diabetes"), ("Mother", "Hypertension"), ("Father", "Coronary artery disease"), ("Mother", "Hypothyroidism"),
          ("Sibling", "Asthma"), ("Mother", "Breast cancer"), ("Father", "Stroke")]
ALLERGIES = [("Penicillin", "drug", "Urticarial rash", "moderate"), ("Sulfonamides", "drug", "Skin rash", "mild"),
             ("Diclofenac", "drug", "Angioedema", "severe"), ("Peanuts", "food", "Lip swelling", "moderate"),
             ("Dust mite", "environmental", "Sneezing, wheeze", "mild"), ("Iodinated contrast", "drug", "Hives after CT contrast", "moderate"),
             ("Shellfish", "food", "Vomiting, hives", "moderate")]


def run(ctx):
    h, pts = ctx["h"], ctx["pts"]
    doc_user = ctx["docs"][0].user if getattr(ctx["docs"][0], "user", None) else None
    # patient 121 (the Patient 360 example) and the first 90 registrations
    adult = [p for p in pts if p.date_of_birth and (TODAY - p.date_of_birth).days > 18 * 365]
    chosen = adult[:90]
    extra = [p for p in pts if p.pk == 121 and p not in chosen]
    n_hist = n_all = 0
    for p in chosen + extra:
        conds = RNG.sample(CONDITIONS, RNG.choice([1, 1, 2, 2, 3]))
        surg = RNG.sample(SURGERIES, RNG.choice([0, 1, 1, 2])) if chance(0.7) else []
        fam = RNG.sample(FAMILY, RNG.choice([1, 2]))
        habits = {"smoking": RNG.choice(["never", "never", "former", "current"]), "alcohol": RNG.choice(["never", "occasional", "occasional", "regular"]),
                  "diet": RNG.choice(["vegetarian", "vegetarian", "mixed", "eggetarian"]), "exercise": RNG.choice(["sedentary", "walks daily", "gym 3x/week"])}
        obstetric = {}
        if p.gender == "female" and chance(0.5):
            g = RNG.randint(1, 3)
            obstetric = {"gravida": g, "para": g, "living": g, "abortions": 0}
        else:
            surg = [s for s in surg if s[0] != "Caesarean section"]
        PatientHistory.objects.update_or_create(hospital=h, patient=p, defaults={
            "past_medical": [{"condition": c, "since": str(y), "ongoing": True, "notes": ""} for c, y in conds],
            "surgical": [{"procedure": s, "year": str(y), "notes": ""} for s, y in surg],
            "family": [{"relation": r, "condition": c} for r, c in fam],
            "habits": habits, "obstetric": obstetric, "updated_by": doc_user})
        n_hist += 1
        if chance(0.35) or p.pk == 121:
            a, typ, reaction, sev = RNG.choice(ALLERGIES)
            Allergy.objects.get_or_create(hospital=h, patient=p, allergen=a, defaults={
                "allergen_type": typ, "reaction": reaction, "severity": sev, "noted_on": days_ago(RNG.randint(60, 900)).date(), "recorded_by": doc_user})
            n_all += 1
    return f"{n_hist} patient histories, {n_all} allergies"
