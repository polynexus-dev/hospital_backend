"""Subscription enforcement: an API request for a module the hospital hasn't
licensed (Hospital.enabled_modules, set in the SaaS console) is refused.

The menu already hides disabled modules; this makes the API agree, so a
module can't be used by calling it directly.

Which module a request belongs to is read from the resolved view's Python
module (apps.<app>...), so every endpoint an app adds is covered without a
list of URLs to maintain. Apps absent from the map are shared by both suites
(patients, appointments, clinical safety, admin, ...) and always allowed.
"""
from django.http import JsonResponse
from django.urls import Resolver404, resolve
from rest_framework import status
from rest_framework.exceptions import APIException, AuthenticationFailed

# Python-module prefix -> module key. Longest prefix wins.
VIEW_MODULES = {
    # CRM suite
    "apps.telephony": "telephony",
    "apps.enquiries": "enquiries",
    "apps.communications": "inbox",
    "apps.referrals": "referrals",
    "apps.packages": "packages",
    "apps.tpa": "tpa",
    "apps.feedback": "feedback",
    "apps.automation": "workflows",
    # HMS suite
    "apps.opd": "opd",
    "apps.ipd": "ipd",
    "apps.nursing": "nursing",
    "apps.laboratory": "laboratory",
    "apps.radiology": "radiology",
    "apps.pharmacy": "pharmacy",
    "apps.emergency": "emergency",
    "apps.ot": "ot",
    "apps.icu": "icu",
    "apps.bloodbank": "bloodbank",
    "apps.finance": "finance",
    "apps.hr": "hr",
    "apps.billing": "billing",
    "apps.inventory": "inventory",
    "apps.telemedicine": "telemedicine",
    "apps.queue_mgmt": "queue",
    "apps.portal": "portal",
    "apps.infection_control": "infection_control",
    "apps.quality": "quality",
    "apps.mrd": "mrd",
    "apps.dietary": "dietary",
    "apps.oncology": "oncology",
    "apps.cathlab": "cathlab",
    "apps.schemes": "schemes",
    "apps.support_services": "support_services",
    "apps.analytics.predictive": "predictive",
    "apps.abdm": "abdm",
}
_PREFIXES = sorted(VIEW_MODULES, key=len, reverse=True)


def disabled_body(module):
    return {"detail": f"The {module.replace('_', ' ')} module isn't part of this hospital's subscription.",
            "code": "module_disabled", "module": module}


class ModuleDisabled(APIException):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "module_disabled"

    def __init__(self, module):
        super().__init__(disabled_body(module))


def module_for_view(view_module_path: str):
    for prefix in _PREFIXES:
        if view_module_path == prefix or view_module_path.startswith(prefix + "."):
            return VIEW_MODULES[prefix]
    return None


def is_enabled(hospital, module) -> bool:
    """An empty list means every module (the long-standing convention)."""
    if module is None or hospital is None:
        return True
    enabled = hospital.enabled_modules or []
    return not enabled or module in enabled


def require_module(hospital, module):
    """For public views (no staff login) that find their hospital themselves."""
    if not is_enabled(hospital, module):
        raise ModuleDisabled(module)


def _view_module(request):
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    view = getattr(match.func, "cls", None) or getattr(match.func, "view_class", None) or match.func
    return module_for_view(getattr(view, "__module__", "") or "")


def _requester(request):
    """(hospital, bypass) for whoever is calling: staff JWT, patient-portal
    token, a session login, or — unauthenticated — the subdomain / X-Tenant."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    user = None
    if header.startswith("Bearer "):
        from rest_framework_simplejwt.authentication import JWTAuthentication

        try:
            result = JWTAuthentication().authenticate(request)
        except AuthenticationFailed:  # includes InvalidToken: the view itself will reject it
            return None, False
        user = result[0] if result else None
    elif header.startswith("Portal "):
        from apps.portal.views import PortalTokenAuthentication

        try:
            result = PortalTokenAuthentication().authenticate(request)
        except AuthenticationFailed:
            return None, False
        return (result[0].hospital if result else None), False
    else:
        session_user = getattr(request, "user", None)
        if session_user is not None and getattr(session_user, "is_authenticated", False):
            user = session_user
    if user is not None:
        if getattr(user, "can_cross_tenant", False) or getattr(user, "is_saas_admin", False):
            return None, True  # platform operators manage every tenant
        return getattr(user, "hospital", None), False
    return getattr(request, "tenant", None), False


class ModuleAccessMiddleware:
    """Refuses API calls to modules the requesting hospital hasn't licensed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith("/api/"):
            module = _view_module(request)
            if module is not None:
                hospital, bypass = _requester(request)
                if not bypass and not is_enabled(hospital, module):
                    return JsonResponse(disabled_body(module), status=403)
        return self.get_response(request)
