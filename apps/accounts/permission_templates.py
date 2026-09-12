"""
Default permission sets applied to a Role when it's created with a known
`template`. Granularity is per-app, not per-model — a hospital's "Front
Desk" role gets full CRUD across the enquiries/patients/appointments/
packages/tpa apps rather than a hand-tuned matrix of 40 individual models.
That's a deliberate simplification: exact per-model policy is a hospital's
own call, and these templates exist to make the product secure and usable
out of the box, not to encode a definitive policy. A hospital admin can
always refine an individual Role's permissions afterward via Django admin's
group permission editor — `template` only decides what a *new* Role starts
with, not an ongoing constraint.

`template=None` (the default) assigns nothing, matching how a hospital
admin hand-building a bespoke role would expect a blank slate.
"""

FULL_ACCESS_APPS = [
    "abdm", "accounts", "analytics", "appointments", "automation", "billing",
    "bloodbank", "communications", "core", "emergency", "enquiries",
    "facilities", "feedback", "finance", "hr", "icu", "integrations",
    "inventory", "ipd", "laboratory", "nursing", "opd", "ot",
    "packages", "patients", "pharmacy", "privacy", "radiology", "referrals",
    "saas_admin", "telephony", "tpa",
]

# app_label -> permission verbs (subset of add/change/delete/view) granted
# across every model in that app.
PERMISSION_TEMPLATES = {
    "owner": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "admin": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "hospital_administrator": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "doctor": {
        "patients": ["view", "add", "change"],
        "abdm": ["view"],
        "appointments": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "nursing": ["view", "add", "change"],
        "laboratory": ["view", "add", "change"],
        "radiology": ["view", "add", "change"],
        "pharmacy": ["view", "add", "change"],
        "emergency": ["view", "add", "change"],
        "ot": ["view", "add", "change"],
        "icu": ["view", "add", "change"],
        "bloodbank": ["view", "add", "change"],
        "communications": ["view", "add"],
        "feedback": ["view"],
        "referrals": ["view"],
        "packages": ["view"],
        "tpa": ["view"],
    },
    "front_desk": {
        "patients": ["view", "add", "change"],
        "abdm": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "packages": ["view", "add", "change"],
        "tpa": ["view", "add", "change"],
        "communications": ["view", "add"],
        "referrals": ["view"],
        "feedback": ["view", "add"],
        "facilities": ["view"],
        "billing": ["view", "add", "change"],
    },
    "receptionist": {
        "patients": ["view", "add", "change"],
        "abdm": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "packages": ["view", "add", "change"],
        "tpa": ["view", "add", "change"],
        "communications": ["view", "add"],
        "referrals": ["view"],
        "feedback": ["view", "add"],
        "facilities": ["view"],
        "billing": ["view", "add", "change"],
    },
    "telephony_operator": {
        "telephony": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "patients": ["view"],
        "communications": ["view", "add"],
    },
    "nurse": {
        "nursing": ["view", "add", "change", "delete"],
        "ipd": ["view"],
        "opd": ["view", "add", "change"],
        "patients": ["view", "add", "change"],
        "pharmacy": ["view", "add", "change"],
        "laboratory": ["view", "add", "change"],
        "radiology": ["view", "add", "change"],
        "bloodbank": ["view", "add", "change"],
        "emergency": ["view", "add", "change"],
        "icu": ["view", "add", "change"],
    },
    "lab_technician": {
        "laboratory": ["view", "add", "change"],
        "patients": ["view"],
        "opd": ["view"],
        "ipd": ["view"],
    },
    "lab_manager": {
        "laboratory": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "opd": ["view"],
        "ipd": ["view"],
    },
    "radiology_technician": {
        "radiology": ["view", "add", "change"],
        "patients": ["view"],
        "opd": ["view"],
        "ipd": ["view"],
    },
    "radiologist": {
        "radiology": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "opd": ["view"],
        "ipd": ["view"],
    },
    "pharmacist": {
        "pharmacy": ["view", "add", "change", "delete"],
        "inventory": ["view", "add", "change"],
        "patients": ["view"],
        "opd": ["view"],
        "ipd": ["view"],
    },
    "ot_manager": {
        "ot": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
    },
    "surgeon": {
        "ot": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
    },
    "anaesthetist": {
        "ot": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
    },
    "icu_staff": {
        "icu": ["view", "add", "change", "delete"],
        "ipd": ["view", "add", "change"],
        "nursing": ["view", "add", "change"],
        "patients": ["view", "add", "change"],
    },
    "blood_bank_technician": {
        "bloodbank": ["view", "add", "change", "delete"],
        "patients": ["view"],
        "ipd": ["view"],
        "emergency": ["view"],
    },
    "finance_manager": {
        "finance": ["view", "add", "change", "delete"],
        "billing": ["view", "add", "change", "delete"],
        "tpa": ["view", "add", "change"],
        "patients": ["view"],
    },
    "hr_manager": {
        "hr": ["view", "add", "change", "delete"],
        "accounts": ["view", "add", "change"],
    },
    "billing_executive": {
        "billing": ["view", "add", "change", "delete"],
        "tpa": ["view", "add", "change", "delete"],
        "packages": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "finance": ["view", "add", "change"],
    },
    "billing_manager": {
        "billing": ["view", "add", "change", "delete"],
        "tpa": ["view", "add", "change", "delete"],
        "packages": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "finance": ["view", "add", "change", "delete"],
    },
    "insurance_tpa_executive": {
        "tpa": ["view", "add", "change", "delete"],
        "abdm": ["view", "add", "change"],
        "billing": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
    },
    "inventory_manager": {
        "inventory": ["view", "add", "change", "delete"],
        "pharmacy": ["view", "add", "change"],
    },
    "purchase_manager": {
        "inventory": ["view", "add", "change", "delete"],
        "pharmacy": ["view", "add", "change"],
    },
}


CLINICAL_ROLES = {
    "owner", "admin", "doctor", "nurse",
    "lab_technician", "lab_manager", "radiology_technician", "radiologist",
    "pharmacist", "ot_manager", "surgeon", "anaesthetist", "icu_staff",
    "blood_bank_technician"
}


VERIFY_PERM_ROLES = {
    "verify_labresult": {"lab_manager", "doctor", "owner", "admin", "hospital_administrator"},
    "verify_radiologyreport": {"radiologist", "doctor", "owner", "admin", "hospital_administrator"},
}


def apply_permission_template(group, template: str) -> None:
    """Assigns every Django `add_<model>`/`change_<model>`/etc. permission
    implied by `template` to `group`, across every model in each listed
    app. No-ops for an unknown template name rather than raising, since a
    hospital naming their own custom role shouldn't crash Role creation."""
    from django.contrib.auth.models import Permission

    app_verbs = PERMISSION_TEMPLATES.get(template)
    if not app_verbs:
        return

    permissions = Permission.objects.filter(content_type__app_label__in=app_verbs.keys())
    to_assign = []
    for perm in permissions:
        verbs = app_verbs.get(perm.content_type.app_label, [])
        codename = perm.codename
        if any(codename.startswith(f"{v}_") for v in verbs):
            to_assign.append(perm)
        elif "change" in verbs or "add" in verbs:
            if codename in VERIFY_PERM_ROLES:
                if template in VERIFY_PERM_ROLES[codename]:
                    to_assign.append(perm)
            elif codename.startswith("finalize_") or codename.startswith("approve_") or codename.startswith("triage_"):
                to_assign.append(perm)

    if template in CLINICAL_ROLES:
        clinical_perm = Permission.objects.filter(content_type__app_label="patients", codename="access_clinical_detail").first()
        if clinical_perm and clinical_perm not in to_assign:
            to_assign.append(clinical_perm)

    group.permissions.add(*to_assign)
