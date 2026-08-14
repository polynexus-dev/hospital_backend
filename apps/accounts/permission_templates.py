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

Each template config is {"apps": {app_label: [verbs]}, "extra": [...]}:
- "apps": the original per-app add/change/delete/view sweep.
- "extra": explicit "app_label.codename" grants for permissions that don't
  fit that sweep — capability flags like patients.access_clinical_detail
  (see docs/erp/03-rbac-and-roles.md §2c) and, from Phase 3 onward,
  action-level permissions like laboratory.verify_labresult (§2a). These
  are deliberately NOT swept in by a "view"/"add" verb match — several of
  the custom codenames this project defines (access_clinical_detail,
  verify_*, finalize_*, approve_*) either don't start with a standard verb
  prefix or, worse, could accidentally start with one (an early version of
  this module's "extra" mechanism didn't exist and a generic app-level
  "view" grant would have silently swept in access_clinical_detail for
  every role with any patients:view access, including front_desk — the
  codename was deliberately chosen to not start with "view_" specifically
  to make that failure mode impossible even if a future template writer
  reintroduces a blanket sweep by mistake).
"""

FULL_ACCESS_APPS = [
    "accounts", "analytics", "appointments", "automation", "communications",
    "core", "enquiries", "facilities", "feedback", "integrations", "ipd", "nursing", "opd", "packages",
    "patients", "referrals", "telephony", "tpa", "emergency", "ot", "icu", "bloodbank",
    "laboratory", "radiology", "pharmacy", "finance", "hr", "billing", "inventory",
]

# Verify/finalize permissions for the diagnostics apps — kept separate from
# CLINICAL_FINALIZE_PERMISSIONS since those belong to doctor's "extra" too
# (a doctor authors/finalizes their own notes) while lab/radiology verification
# is a pathologist/radiologist-specific act a doctor doesn't perform.
DIAGNOSTIC_VERIFY_PERMISSIONS = ["laboratory.verify_labresult", "radiology.verify_radiologyreport"]

# Custom finalize-permission codenames granted wholesale to owner/admin/
# doctor's "extra" below — kept as one list so a new clinical model's
# finalize permission (Phase 5's laboratory.finalize_labresult, etc.) is
# added once here instead of edited into three template entries by hand.
CLINICAL_FINALIZE_PERMISSIONS = [
    "opd.finalize_clinicalnote", "opd.finalize_diagnosis",
    "ipd.finalize_doctorprogressnote", "ipd.finalize_dischargesummary",
    "ot.finalize_operativenote", "ot.finalize_anaesthesiarecord",
    "icu.finalize_icudailyprogressnote",
]

FULL_VERBS = ["add", "change", "delete", "view"]

# Apps that make up the CRM domain (docs/erp/00-overview.md) — used to
# build the CRM role templates below without repeating the same app list
# seven times.
CRM_APPS = ["enquiries", "communications", "automation", "feedback", "analytics", "packages", "referrals", "telephony"]

PERMISSION_TEMPLATES = {
    "owner": {
        "apps": {app: FULL_VERBS for app in FULL_ACCESS_APPS},
        "extra": ["patients.access_clinical_detail", *CLINICAL_FINALIZE_PERMISSIONS, *DIAGNOSTIC_VERIFY_PERMISSIONS],
    },
    "admin": {
        "apps": {app: FULL_VERBS for app in FULL_ACCESS_APPS},
        "extra": ["patients.access_clinical_detail", *CLINICAL_FINALIZE_PERMISSIONS, *DIAGNOSTIC_VERIFY_PERMISSIONS],
    },
    "doctor": {
        "apps": {
            "patients": ["view", "add", "change"],
            "appointments": ["view", "add", "change"],
            "opd": ["view", "add", "change"],
            "ipd": ["view", "add", "change"],
            "emergency": ["view", "add", "change"],
            "ot": ["view", "add", "change"],
            "icu": ["view", "add", "change"],
            "bloodbank": ["view"],
            "communications": ["view", "add"],
            "feedback": ["view"],
            "referrals": ["view"],
            "packages": ["view"],
            "tpa": ["view"],
        },
        "extra": ["patients.access_clinical_detail", *CLINICAL_FINALIZE_PERMISSIONS],
    },
    "front_desk": {
        "apps": {
            "patients": ["view", "add", "change"],
            "enquiries": ["view", "add", "change"],
            "appointments": ["view", "add", "change"],
            "packages": ["view", "add", "change"],
            "tpa": ["view", "add", "change"],
            "communications": ["view", "add"],
            "referrals": ["view"],
            "feedback": ["view", "add"],
        },
    },
    "telephony_operator": {
        "apps": {
            "telephony": ["view", "add", "change"],
            "enquiries": ["view", "add", "change"],
            "patients": ["view"],
            "communications": ["view", "add"],
        },
    },

    # --- ERP role templates (docs/erp/03-rbac-and-roles.md §3) ---------
    "receptionist": {
        # ERP-scoped counterpart to front_desk — registration, appointments,
        # facilities/bed visibility. Deliberately no "opd" app access at
        # all and no access_clinical_detail: "Cannot ... access confidential
        # clinical information unnecessarily" (§3's Receptionist row). A
        # hospital that wants one combined CRM+ERP front-desk role should
        # use front_desk, not this — they're separate templates precisely
        # so a hospital can choose narrower ERP-only access when they want it.
        "apps": {
            "patients": ["view", "add", "change"],
            "appointments": ["view", "add", "change"],
            "facilities": ["view"],
        },
    },
    "hospital_administrator": {
        "apps": {
            "facilities": FULL_VERBS,
            "appointments": ["view"],
            "opd": ["view"],
            "ipd": ["view"],
            "nursing": ["view"],
            "emergency": ["view", "add", "change"],
            "ot": ["view", "add", "change"],
            "icu": ["view", "add", "change"],
            "bloodbank": FULL_VERBS,
            "patients": ["view"],
            "accounts": ["view"],
            "analytics": ["view"],
        },
        "extra": ["patients.access_clinical_detail"],
    },
    "nurse": {
        # opd/ipd are view-only here, deliberately — this module's own
        # per-app (not per-model) sweep granularity means "add" on opd
        # would also grant add_clinicalnote/add_diagnosis, not just
        # add_vitalsreading, and write access to doctor-authored clinical
        # content is meant to stay doctor-only (see
        # docs/erp/03-rbac-and-roles.md §4's permission matrix: Nurse gets
        # view-only on OPD/Encounter). A nurse recording vitals during an
        # OPD consult is a real gap this simplification creates — noted,
        # not silently worked around; a hospital that needs it can grant
        # opd.add_vitalsreading directly via Django admin's per-permission
        # editor, same as any template can be refined post-creation.
        "apps": {
            "patients": ["view"],
            "opd": ["view"],
            "ipd": ["view"],
            "nursing": ["view", "add", "change"],
            "facilities": ["view"],
        },
        "extra": ["patients.access_clinical_detail"],
    },
    "ot_manager": {
        "apps": {
            "ot": FULL_VERBS,
            "facilities": ["view"],
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "ot.finalize_operativenote", "ot.finalize_anaesthesiarecord"],
    },
    "surgeon": {
        "apps": {
            "ot": ["view", "add", "change"],
            "patients": ["view"],
            "opd": ["view"],
            "ipd": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "ot.finalize_operativenote"],
    },
    "anaesthetist": {
        "apps": {
            "ot": ["view", "add", "change"],
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "ot.finalize_anaesthesiarecord"],
    },
    "icu_staff": {
        "apps": {
            "icu": ["view", "add", "change"],
            "facilities": ["view"],
            "patients": ["view"],
            "ipd": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "icu.finalize_icudailyprogressnote"],
    },
    "lab_technician": {
        # Collects samples, runs tests, enters results — does not verify
        # them (see laboratory.LabResultViewSet.verify's action_permissions
        # gate on laboratory.verify_labresult, deliberately withheld here).
        "apps": {
            "laboratory": ["view", "add", "change"],
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail"],
    },
    "lab_manager": {
        "apps": {
            "laboratory": FULL_VERBS,
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "laboratory.verify_labresult"],
    },
    "radiology_technician": {
        "apps": {
            "radiology": ["view", "add", "change"],
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail"],
    },
    "radiologist": {
        "apps": {
            "radiology": FULL_VERBS,
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail", "radiology.verify_radiologyreport"],
    },
    "pharmacist": {
        "apps": {
            "pharmacy": FULL_VERBS,
            "patients": ["view"],
        },
        "extra": ["patients.access_clinical_detail"],
    },

    # --- CRM role templates (docs/erp/03-rbac-and-roles.md §3) ---------
    # None of these get patients.access_clinical_detail — CRM roles see
    # Patient's demographic/contact/insurance fields (PatientSerializer)
    # but never national_id_number or any future clinical-content model
    # (Prescription, and later Encounter/Diagnosis) — see
    # apps.patients.views.PatientViewSet.get_serializer_class and
    # RequiresClinicalDetailPermission on PrescriptionViewSet.
    "crm_super_admin": {
        "apps": {
            **{app: FULL_VERBS for app in CRM_APPS},
            "accounts": FULL_VERBS,
            "patients": ["view", "add", "change"],
        },
    },
    "crm_manager": {
        "apps": {
            "enquiries": ["view", "add", "change"],
            "automation": ["view", "add", "change"],
            "packages": ["view", "add", "change"],
            "referrals": ["view", "add", "change"],
            "analytics": ["view"],
            "communications": ["view", "add"],
            "patients": ["view"],
        },
    },
    "crm_executive": {
        "apps": {
            "enquiries": ["view", "add", "change"],
            "automation": ["view", "add", "change"],
            "communications": ["view", "add"],
            "patients": ["view"],
        },
    },
    "call_centre_executive": {
        "apps": {
            "telephony": ["view", "add", "change"],
            "enquiries": ["view", "add", "change"],
            "communications": ["view", "add"],
            "patients": ["view"],
        },
    },
    "marketing_manager": {
        "apps": {
            "packages": ["view", "add", "change"],
            "analytics": ["view"],
            "enquiries": ["view"],
        },
    },
    "corporate_rm": {
        "apps": {
            "packages": ["view", "add", "change"],
            "referrals": ["view", "add", "change"],
            "enquiries": ["view"],
        },
    },
    "crm_auditor": {
        "apps": {app: ["view"] for app in CRM_APPS},
    },
    "finance_manager": {
        "apps": {
            "finance": ["view", "add", "change"],
            "analytics": ["view"],
            "tpa": ["view"],
        },
    },
    "hr_manager": {
        "apps": {
            "hr": ["view", "add", "change"],
            "accounts": ["view"],
            "facilities": ["view"],
        },
    },
    "him_officer": {
        "apps": {
            "patients": ["view"],
            "ipd": ["view"],
            "opd": ["view"],
            "emergency": ["view"],
        },
    },
    "billing_executive": {
        "apps": {
            "billing": ["view", "add", "change"],
            "patients": ["view"],
            "tpa": ["view"],
        },
    },
    "billing_manager": {
        "apps": {
            "billing": FULL_VERBS,
            "tpa": FULL_VERBS,
            "patients": ["view"],
        },
    },
    "insurance_tpa_executive": {
        "apps": {
            "billing": ["view", "add", "change"],
            "tpa": FULL_VERBS,
            "patients": ["view"],
        },
    },
    "inventory_manager": {
        "apps": {
            "inventory": FULL_VERBS,
            "facilities": ["view"],
        },
    },
    "purchase_manager": {
        "apps": {
            "inventory": ["view", "add", "change"],
            "finance": ["view"],
        },
    },
}


def apply_permission_template(group, template: str) -> None:
    """Assigns every Django `add_<model>`/`change_<model>`/etc. permission
    implied by `template`'s "apps" sweep, plus any explicitly-named
    "extra" permission, to `group`. No-ops for an unknown template name
    rather than raising, since a hospital naming their own custom role
    shouldn't crash Role creation."""
    from django.contrib.auth.models import Permission

    config = PERMISSION_TEMPLATES.get(template)
    if not config:
        return

    app_verbs = config.get("apps", {})
    permissions = Permission.objects.filter(content_type__app_label__in=app_verbs.keys())
    to_assign = [
        perm for perm in permissions
        for verb in app_verbs.get(perm.content_type.app_label, [])
        if perm.codename.startswith(f"{verb}_")
    ]

    extra_codenames = config.get("extra", [])
    if extra_codenames:
        pairs = [codename.split(".", 1) for codename in extra_codenames]
        app_labels = {app_label for app_label, _ in pairs}
        codenames = {codename for _, codename in pairs}
        to_assign += list(Permission.objects.filter(content_type__app_label__in=app_labels, codename__in=codenames))

    group.permissions.add(*to_assign)
