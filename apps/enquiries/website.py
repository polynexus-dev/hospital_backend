"""Website enquiry form, added to a hospital's own website with one script
tag (and, with chat_enabled, the live-chat bubble).

    <script src="https://<host>/api/v1/site/<site_key>/widget.js" async></script>
    <div data-polynexus-enquiry></div>      <- optional: inline form here;
                                               otherwise a floating button

Public endpoints, so every request is checked:
- the page's Origin must be one of the hospital's allowed domains;
- a hidden "website" field (bots fill it) and a minimum fill time;
- per-IP and per-mobile rate limits;
- explicit consent (DPDP Act 2023), recorded on the enquiry.
The widget posts text/plain JSON so browsers send it without a CORS preflight.
"""
import json
import re
from pathlib import Path

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.encryption import blind_index, normalize_phone
from apps.core.modules import require_module

from .models import Enquiry, WebsiteWidget

WIDGET_JS = Path(__file__).resolve().parent / "widget" / "widget.js"
MOBILE = re.compile(r"^[6-9]\d{9}$")
MIN_FILL_SECONDS = 3
PER_IP_LIMIT = (5, 600)          # 5 enquiries per 10 minutes per IP
PER_MOBILE_LIMIT = (3, 86400)    # 3 per day per mobile number


def _widget(site_key):
    w = WebsiteWidget.objects.filter(site_key=site_key, hospital__is_active=True).select_related("hospital").first()
    if w is not None:
        require_module(w.hospital, "enquiries")
    return w


def _origin_ok(request, widget, origin):
    from urllib.parse import urlparse

    own = urlparse(origin).netloc == request.get_host()  # our own preview page
    return own or widget.origin_allowed(origin)


def _cors(response, request, widget):
    origin = request.headers.get("Origin", "")
    if origin and _origin_ok(request, widget, origin):
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
    return response


def _client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", "")


def _over_limit(key, limit):
    count, window = limit
    n = cache.get(key, 0)
    if n >= count:
        return True
    cache.set(key, n + 1, window)
    return False


def _services(widget):
    if widget.services:
        return [str(s) for s in widget.services if str(s).strip()]
    from apps.core.models import Department

    return list(Department.objects.filter(hospital=widget.hospital).order_by("name").values_list("name", flat=True))


def public_config(widget):
    h = widget.hospital
    return {
        "hospital": h.name, "title": widget.title, "intro": widget.intro, "button_text": widget.button_text,
        "accent_color": widget.accent_color, "services": _services(widget),
        "consent_text": (widget.consent_text or WebsiteWidget.DEFAULT_CONSENT).replace("{hospital}", h.name),
        "thank_you_message": widget.thank_you_message, "form_enabled": widget.form_enabled, "chat_enabled": widget.chat_enabled,
        "chat_welcome": widget.chat_welcome,
    }


class PublicView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []


class SiteConfigView(PublicView):
    def get(self, request, site_key):
        w = _widget(site_key)
        if w is None:
            return Response({"detail": "Unknown site key."}, status=404)
        if request.headers.get("Origin") and not _origin_ok(request, w, request.headers["Origin"]):
            return Response({"detail": "This website isn't allowed to use this form."}, status=403)
        return _cors(Response(public_config(w)), request, w)


class SiteEnquiryView(PublicView):
    """POST text/plain JSON: name, mobile, email?, service?, message?,
    consent (true), started_at (ms), website (honeypot, must be empty),
    utm_*?, landing_page?, referrer?, gclid?, fbclid?."""

    def post(self, request, site_key):
        w = _widget(site_key)
        if w is None or not w.form_enabled:
            return Response({"detail": "Unknown site key."}, status=404)
        origin = request.headers.get("Origin", "")
        if not origin or not _origin_ok(request, w, origin):
            return Response({"detail": "This website isn't allowed to use this form."}, status=403)
        try:
            data = json.loads(request.body or b"{}")
            assert isinstance(data, dict)
        except (ValueError, AssertionError):
            return _cors(Response({"detail": "Invalid form data."}, status=400), request, w)

        def text(key, n):
            return str(data.get(key) or "").strip()[:n]

        # Bots: filled the hidden field, or submitted faster than a person can type.
        started = data.get("started_at")
        too_fast = isinstance(started, (int, float)) and (timezone.now().timestamp() * 1000 - started) < MIN_FILL_SECONDS * 1000
        if text("website", 200) or too_fast:
            return _cors(Response({"ok": True, "message": w.thank_you_message}, status=201), request, w)  # look normal to the bot

        name, mobile = text("name", 120), normalize_phone(text("mobile", 20))
        errors = {}
        if len(name) < 2:
            errors["name"] = "Please enter your name."
        if not MOBILE.match(mobile):
            errors["mobile"] = "Please enter a valid 10-digit mobile number."
        email = text("email", 254)
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errors["email"] = "Please enter a valid email, or leave it empty."
        if data.get("consent") is not True:
            errors["consent"] = "Please tick the consent box so we can contact you."
        if errors:
            return _cors(Response(errors, status=400), request, w)

        if _over_limit(f"siteform:ip:{w.pk}:{_client_ip(request)}", PER_IP_LIMIT) or \
                _over_limit(f"siteform:m:{w.pk}:{blind_index(mobile)}", PER_MOBILE_LIMIT):
            return _cors(Response({"detail": "We've already received your enquiry. Our team will call you shortly."}, status=429), request, w)

        consent = public_config(w)["consent_text"]
        notes = [text("message", 1000)] if text("message", 1000) else []
        notes.append(f"Consent given on the website form at {timezone.localtime():%d %b %Y %H:%M}: \"{consent}\"")
        for click in ("gclid", "fbclid"):
            if text(click, 200):
                notes.append(f"{click}: {text(click, 200)}")
        landing = text("landing_page", 500)
        referrer = text("referrer", 500)
        enquiry = Enquiry.objects.create(
            hospital=w.hospital, name=name, mobile=mobile, email=email, source=Enquiry.Source.WEBSITE,
            service_requested=text("service", 255), notes="\n".join(notes),
            utm_source=text("utm_source", 100), utm_medium=text("utm_medium", 100), utm_campaign=text("utm_campaign", 150),
            utm_term=text("utm_term", 150), utm_content=text("utm_content", 150), campaign=text("utm_campaign", 150),
            landing_page=landing if landing.startswith(("http://", "https://")) else "",
            referrer_url=referrer if referrer.startswith(("http://", "https://")) else "",
        )
        return _cors(Response({"ok": True, "id": enquiry.pk, "message": w.thank_you_message}, status=201), request, w)


class WidgetScriptView(PublicView):
    def get(self, request, site_key):
        w = _widget(site_key)
        if w is None:
            return HttpResponse("/* Unknown site key */", content_type="application/javascript", status=404)
        api = request.build_absolute_uri(f"/api/v1/site/{site_key}/")
        script = WIDGET_JS.read_text(encoding="utf-8").replace("__SITE_API__", api)
        resp = HttpResponse(script, content_type="application/javascript; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=300"
        return resp


class WidgetPreviewView(PublicView):
    """A blank page with the widget, for staff to try it before adding it to
    the hospital's site. Uses the backend's own origin, so it's allowed."""

    def get(self, request, site_key):
        w = _widget(site_key)
        if w is None:
            return HttpResponse("Unknown site key", status=404)
        src = request.build_absolute_uri(f"/api/v1/site/{site_key}/widget.js")
        html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website form preview</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#1f2937}}</style></head>
<body><h1>{w.hospital.name}: website preview</h1><p>This is how the form looks on your website. Enquiries sent from here are real and appear in the CRM.</p>
<div data-polynexus-enquiry></div><script src="{src}" async></script></body></html>"""
        return HttpResponse(html)


# Hospital admins plus the CRM and marketing leads. Front-line roles can edit
# enquiries but not the public form that feeds them.
WIDGET_MANAGERS = {"owner", "admin", "hospital_administrator", "crm_super_admin", "crm_manager", "marketing_manager"}


def _manage_allowed(user):
    return user.is_superuser or getattr(getattr(user, "role", None), "template", "") in WIDGET_MANAGERS


class WebsiteWidgetAdminView(APIView):
    """Staff: GET (created on first use) / PATCH the hospital's widget."""

    permission_classes = [permissions.IsAuthenticated]
    EDITABLE = ("allowed_domains", "form_enabled", "chat_enabled", "title", "intro", "button_text", "accent_color", "services", "consent_text",
                "thank_you_message", "chat_welcome", "chat_auto_reply")

    def _payload(self, request, w):
        script = request.build_absolute_uri(f"/api/v1/site/{w.site_key}/widget.js")
        return {
            **{k: getattr(w, k) for k in self.EDITABLE}, "site_key": w.site_key,
            "embed_code": f'<script src="{script}" async></script>\n<div data-polynexus-enquiry></div>',
            "preview_url": request.build_absolute_uri(f"/api/v1/site/{w.site_key}/preview/"),
        }

    def get(self, request):
        if not _manage_allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        w, _ = WebsiteWidget.objects.get_or_create(hospital_id=request.user.hospital_id)
        return Response(self._payload(request, w))

    def patch(self, request):
        if not _manage_allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        w, _ = WebsiteWidget.objects.get_or_create(hospital_id=request.user.hospital_id)
        data = request.data
        if "allowed_domains" in data:
            domains = data["allowed_domains"] if isinstance(data["allowed_domains"], list) else str(data["allowed_domains"]).replace(",", "\n").split()
            cleaned = []
            for d in domains:
                d = re.sub(r"^https?://", "", str(d).strip().lower()).split("/")[0]
                if d and not re.match(r"^[a-z0-9.-]+(:\d+)?$", d):
                    return Response({"allowed_domains": f"'{d}' isn't a domain name."}, status=400)
                if d:
                    cleaned.append(d)
            w.allowed_domains = cleaned
        if "accent_color" in data and not re.match(r"^#[0-9a-fA-F]{6}$", str(data["accent_color"])):
            return Response({"accent_color": "Use a colour like #0f766e."}, status=400)
        if "services" in data:
            w.services = [str(s).strip()[:80] for s in (data["services"] or []) if str(s).strip()][:40]
        for k in ("form_enabled", "chat_enabled"):
            if k in data:
                setattr(w, k, bool(data[k]))
        for k in ("title", "intro", "button_text", "accent_color", "consent_text", "thank_you_message", "chat_welcome", "chat_auto_reply"):
            if k in data:
                setattr(w, k, str(data[k])[: WebsiteWidget._meta.get_field(k).max_length or 2000])
        w.save()
        return Response(self._payload(request, w), status=status.HTTP_200_OK)

    def post(self, request):
        """Rotate the site key (old embed code stops working)."""
        if not _manage_allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        from .models import _site_key

        w, _ = WebsiteWidget.objects.get_or_create(hospital_id=request.user.hospital_id)
        w.site_key = _site_key()
        w.save(update_fields=["site_key"])
        return Response(self._payload(request, w))
