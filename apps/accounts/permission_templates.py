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
    "saas_admin", "telephony", "tpa", "governance", "clinical", "infection_control", "quality", "support_services", "queue_mgmt", "telemedicine", "portal", "mrd", "dietary", "oncology", "schemes", "cathlab",
]

# app_label -> permission verbs (subset of add/change/delete/view) granted
# across every model in that app.
PERMISSION_TEMPLATES = {
    "owner": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "admin": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "hospital_administrator": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "doctor": {
        # Full CRUD on patients — covers PatientViewSet, DocumentViewSet,
        # and PrescriptionViewSet (which also needs RequiresClinicalDetailPermission
        # satisfied via the "patients.access_clinical_detail" perm added below by
        # CLINICAL_ROLES).  delete is intentional: OPD doctors may need to
        # remove a duplicate record or an incorrectly uploaded document.
        "patients": ["view", "add", "change", "delete"],
        "clinical": ["view", "add", "change", "delete"],
        "abdm": ["view", "add", "change"],
        "appointments": ["view", "add", "change", "delete"],
        "opd": ["view", "add", "change", "delete"],
        "ipd": ["view", "add", "change"],
        "nursing": ["view", "add", "change"],
        "laboratory": ["view", "add", "change"],
        "radiology": ["view", "add", "change"],
        "pharmacy": ["view", "add", "change"],
        "emergency": ["view", "add", "change"],
        "ot": ["view", "add", "change"],
        "cathlab": ["view", "add", "change"],
        "icu": ["view", "add", "change"],
        "bloodbank": ["view", "add", "change"],
        "communications": ["view", "add"],
        "feedback": ["view"],
        "referrals": ["view", "add", "change"],
        "packages": ["view"],
        "tpa": ["view"],
        "schemes": ["view"],
    },
    "front_desk": {
        "patients": ["view", "add", "change"],
        "abdm": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "packages": ["view", "add", "change"],
        "tpa": ["view", "add", "change"],
        "schemes": ["view", "add", "change"],
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
        "schemes": ["view", "add", "change"],
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
    # CRM roles. Before these existed the CRM templates granted nothing, so a
    # "CRM Executive" couldn't open an enquiry.
    "crm_super_admin": {
        **{app: ["view", "add", "change", "delete"] for app in ("enquiries", "telephony", "communications", "automation", "packages", "referrals", "feedback")},
        "patients": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "analytics": ["view"],
        "tpa": ["view"],
        "schemes": ["view"],
    },
    "crm_manager": {
        **{app: ["view", "add", "change"] for app in ("enquiries", "telephony", "communications", "automation", "packages", "referrals", "feedback")},
        "patients": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "analytics": ["view"],
    },
    "crm_executive": {
        "enquiries": ["view", "add", "change"],
        "telephony": ["view", "add", "change"],
        "communications": ["view", "add"],
        "automation": ["view", "change"],
        "patients": ["view", "add"],
        "appointments": ["view", "add", "change"],
        "packages": ["view"],
        "referrals": ["view"],
        "feedback": ["view", "add"],
    },
    "call_centre_executive": {
        "telephony": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "communications": ["view", "add"],
        "automation": ["view", "change"],
        "patients": ["view"],
        "appointments": ["view", "add", "change"],
    },
    "marketing_manager": {
        "packages": ["view", "add", "change", "delete"],
        "communications": ["view", "add", "change"],
        "referrals": ["view", "add", "change"],
        "enquiries": ["view"],
        "feedback": ["view"],
        "analytics": ["view"],
    },
    "corporate_rm": {
        "packages": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "referrals": ["view", "add", "change"],
        "patients": ["view"],
        "tpa": ["view"],
    },
    "crm_auditor": {app: ["view"] for app in ("enquiries", "telephony", "communications", "automation", "packages", "referrals", "feedback", "analytics")},
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
        "cathlab": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
    },
    "surgeon": {
        # their own OPD clinic: appointments + clinical records (scoped to their
        # patients by Role.data_scope=assigned_only)
        "appointments": ["view", "change"],
        "clinical": ["view", "add", "change"],
        "ot": ["view", "add", "change", "delete"],
        "cathlab": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
        # read the pre-op work-up results (ordered from the OT screen)
        "laboratory": ["view"],
        "radiology": ["view"],
    },
    "anaesthetist": {
        # their own OPD clinic: appointments + clinical records (scoped to their
        # patients by Role.data_scope=assigned_only)
        "appointments": ["view", "change"],
        "clinical": ["view", "add", "change"],
        "ot": ["view", "add", "change", "delete"],
        "cathlab": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "ipd": ["view", "add", "change"],
        "opd": ["view", "add", "change"],
        "laboratory": ["view"],
        "radiology": ["view"],
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
        "schemes": ["view", "add", "change"],
        "patients": ["view"],
    },
    "hr_manager": {
        "hr": ["view", "add", "change", "delete"],
        "accounts": ["view", "add", "change"],
    },
    "billing_executive": {
        "billing": ["view", "add", "change", "delete"],
        "tpa": ["view", "add", "change", "delete"],
        "schemes": ["view", "add", "change", "delete"],
        "packages": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "finance": ["view", "add", "change"],
    },
    "billing_manager": {
        "billing": ["view", "add", "change", "delete"],
        "tpa": ["view", "add", "change", "delete"],
        "schemes": ["view", "add", "change", "delete"],
        "packages": ["view", "add", "change", "delete"],
        "patients": ["view", "add", "change"],
        "finance": ["view", "add", "change", "delete"],
    },
    "insurance_tpa_executive": {
        "tpa": ["view", "add", "change", "delete"],
        "schemes": ["view", "add", "change", "delete"],
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
    # Discounts above the front-desk limit: managers, never the counter that asks.
    "authorize_discount": {"owner", "admin", "hospital_administrator", "billing_manager", "finance_manager"},
    # Putting a bill on hold stops discharge and collections: managers only.
    "hold_bill": {"owner", "admin", "hospital_administrator", "billing_manager", "finance_manager", "insurance_tpa_executive"},
    # Inactive patient insurance stays out of everyday lists; these roles can see and reactivate it.
    "manage_inactive_insurance": {"owner", "admin", "hospital_administrator", "billing_manager", "finance_manager", "insurance_tpa_executive"},
    # Locking finalises a bill; reopening one is rarer and kept to managers ("Unfreeze bill").
    "lock_bill": {"owner", "admin", "hospital_administrator", "billing_manager", "billing_executive", "finance_manager", "insurance_tpa_executive"},
    "unlock_bill": {"owner", "admin", "hospital_administrator", "billing_manager", "finance_manager"},
    # The checker in maker-checker vouchers: not every accounts user.
    "approve_journalentry": {"owner", "admin", "hospital_administrator", "finance_manager"},
}


# Reference data nearly every screen reads: department, staff and doctor
# pickers, slots, and the lab / medicine / imaging catalogues. Not patient
# or appointment records — roles without those apps (HR, finance) must not
# read them. Reads require `view_*` (apps.core.permissions.
# RoleBasedModelPermissions), so every template starts with these, and a
# user with no role gets them too (backends.HospitalPermissionBackend); a
# hospital admin can still untick them per role.
BASELINE_VIEW_MODELS = [
    ("core", "department"),
    ("accounts", "user"),
    ("accounts", "role"),
    ("appointments", "doctor"),
    ("appointments", "slot"),
    ("laboratory", "labtest"),
    ("pharmacy", "medicine"),
    ("radiology", "radiologyprocedure"),
    # OT tariff templates: read by the OT screens (grade on a request, charge preview).
    ("finance", "surgerygrade"),
    ("finance", "ottariff"),
]


BASELINE_VIEW_CODES = frozenset(f"{app}.view_{model}" for app, model in BASELINE_VIEW_MODELS)


def baseline_view_permissions() -> list:
    from django.db.models import Q
    from django.contrib.auth.models import Permission

    query = Q(pk__in=[])
    for app, model in BASELINE_VIEW_MODELS:
        query |= Q(content_type__app_label=app, content_type__model=model, codename=f"view_{model}")
    return list(Permission.objects.filter(query))


ADMIN_MASTER_TEMPLATES = frozenset({"owner", "admin", "hospital_administrator"})
DOCTOR_MASTER_CODENAMES = frozenset({"add_doctor", "change_doctor", "delete_doctor"})

# Clinician roles that, by default, see only their own appointments, patients,
# OPD visits and admissions (Role.data_scope = assigned_only).
OWN_RECORDS_TEMPLATES = frozenset({"doctor", "surgeon", "anaesthetist"})


def apply_permission_template(group, template: str) -> None:
    """Assigns every Django `add_<model>`/`change_<model>`/etc. permission
    implied by `template` to `group`, across every model in each listed
    app. No-ops for an unknown template name rather than raising, since a
    hospital naming their own custom role shouldn't crash Role creation."""
    to_assign = template_permissions(template)
    if to_assign:
        group.permissions.add(*to_assign)


def template_permissions(template: str) -> list:
    """The Permission rows `template` grants (empty for an unknown template)."""
    from django.contrib.auth.models import Permission

    app_verbs = PERMISSION_TEMPLATES.get(template)
    if not app_verbs:
        return []

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

    # Doctor profiles are master data: only hospital admins add, edit or remove
    # them. Every other template with write access to "appointments" (doctor,
    # front desk, CRM, call centre…) keeps appointments, slots and waitlists.
    if template not in ADMIN_MASTER_TEMPLATES:
        to_assign = [p for p in to_assign if not (p.content_type.app_label == "appointments" and p.codename in DOCTOR_MASTER_CODENAMES)]

    to_assign += [p for p in baseline_view_permissions() if p not in to_assign]

    if template in CLINICAL_ROLES:
        clinical_perm = Permission.objects.filter(content_type__app_label="patients", codename="access_clinical_detail").first()
        if clinical_perm and clinical_perm not in to_assign:
            to_assign.append(clinical_perm)

    return to_assign
