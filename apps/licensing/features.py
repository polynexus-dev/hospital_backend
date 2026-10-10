"""Licensable features and the modules each one switches on.

Pure Python (no Django) so the offline licence issuer in tools/ shares it.
A licence lists feature keys; the product turns them into module keys
(apps.core.models.ALL_MODULES) for the menu and API gating that already
exists, and HasFeature checks the ones without a module of their own.
"""

# key -> (label shown in the issuer and UI, modules it enables)
FEATURES = {
    "hms_core": ("HMS core (OPD, IPD, beds, billing)", [
        "opd", "ipd", "nursing", "billing", "emergency", "ot", "icu", "laboratory", "radiology", "bloodbank",
        "queue", "dietary", "infection_control", "mrd", "oncology", "cathlab", "telemedicine", "schemes",
    ]),
    "crm": ("CRM (enquiries, call queue, callbacks, follow-ups)", [
        "enquiries", "telephony", "referrals", "packages", "feedback", "workflows", "tpa",
    ]),
    "erp": ("ERP (inventory, pharmacy, procurement, accounts)", ["inventory", "pharmacy", "finance", "hr", "support_services"]),
    "mis": ("MIS (dashboards, reports)", ["predictive"]),
    "whatsapp": ("WhatsApp messaging", ["inbox"]),
    "ivr": ("IVR integration", []),
    "ai_assist": ("AI assist (local models)", []),
    "nabh": ("NABH reporting", ["quality"]),
    "multi_branch": ("Multi-branch", []),
    "patient_portal": ("Patient portal", ["portal"]),
    "api_access": ("API access", ["abdm"]),
}

FEATURE_KEYS = list(FEATURES)

# Features that reach outside the building. On-premise installations only
# make these outbound calls when the licence includes the feature.
OUTBOUND_FEATURES = {"whatsapp", "ivr", "api_access"}


def modules_for(features):
    """Module keys enabled by `features` (unknown keys are ignored)."""
    modules = []
    for key in features:
        for module in FEATURES.get(key, ("", []))[1]:
            if module not in modules:
                modules.append(module)
    return modules


def label(key):
    return FEATURES.get(key, (key, []))[0]
