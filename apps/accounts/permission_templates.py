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
    "accounts", "analytics", "appointments", "automation", "communications",
    "core", "enquiries", "feedback", "integrations", "packages", "patients",
    "referrals", "telephony", "tpa",
]

# app_label -> permission verbs (subset of add/change/delete/view) granted
# across every model in that app.
PERMISSION_TEMPLATES = {
    "owner": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "admin": {app: ["add", "change", "delete", "view"] for app in FULL_ACCESS_APPS},
    "doctor": {
        "patients": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "communications": ["view", "add"],
        "feedback": ["view"],
        "referrals": ["view"],
        "packages": ["view"],
        "tpa": ["view"],
    },
    "front_desk": {
        "patients": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "appointments": ["view", "add", "change"],
        "packages": ["view", "add", "change"],
        "tpa": ["view", "add", "change"],
        "communications": ["view", "add"],
        "referrals": ["view"],
        "feedback": ["view", "add"],
    },
    "telephony_operator": {
        "telephony": ["view", "add", "change"],
        "enquiries": ["view", "add", "change"],
        "patients": ["view"],
        "communications": ["view", "add"],
    },
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
    to_assign = [
        perm for perm in permissions
        for verb in app_verbs.get(perm.content_type.app_label, [])
        if perm.codename.startswith(f"{verb}_")
    ]
    group.permissions.add(*to_assign)
