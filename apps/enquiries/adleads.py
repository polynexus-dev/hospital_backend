"""Leads straight from ad platforms into the CRM.

Meta (Facebook / Instagram) lead ads
    Webhook: /api/v1/leads/meta/<hospital id>/   (field: leadgen)
    GET is Meta's subscription check (hub.verify_token). POST carries only a
    lead id, signed with the hospital's app secret (X-Hub-Signature-256); the
    lead's answers are then fetched from the Graph API with the page token.

Google Ads lead form extensions
    Webhook: /api/v1/leads/google/<hospital id>/   with our generated key.
    Google posts the answers directly (user_column_data).

Each platform lead id is logged in AdLead, so a retried delivery never makes a
second enquiry; a Meta lead that couldn't be fetched stays in the log with the
error and can be fetched again from Enquiries > Ad lead forms.
"""
import hashlib
import hmac
import json
import logging

import requests
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.encryption import normalize_phone
from apps.core.modules import require_module

from .models import AdLead, AdLeadIntegration, Enquiry
from .website import WIDGET_MANAGERS

logger = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com/v19.0"
LEAD_FIELDS = "created_time,field_data,ad_name,adset_name,campaign_name,form_id,platform,is_organic"


def _integration(hospital_id):
    i = AdLeadIntegration.objects.filter(hospital_id=hospital_id, hospital__is_active=True).select_related("hospital").first()
    if i is not None:
        require_module(i.hospital, "enquiries")
    return i


def _pick(answers: dict, *keys):
    for key in keys:
        for k, v in answers.items():
            if key in k and v:
                return v
    return ""


def create_enquiry(hospital, *, platform, external_id, answers, campaign="", form_id="", medium="", test=False, extra_notes=()):
    """answers: {normalised question name: answer}. Returns the AdLead row."""
    name = _pick(answers, "full_name", "name")
    if not name:
        name = " ".join(x for x in (_pick(answers, "first_name"), _pick(answers, "last_name")) if x).strip()
    mobile = normalize_phone(_pick(answers, "phone", "mobile", "contact"))
    email = _pick(answers, "email")
    service = _pick(answers, "service", "department", "speciality", "specialty", "treatment", "concern")
    city = _pick(answers, "city")
    known = {"full_name", "first_name", "last_name", "phone_number", "email", "city"}
    other = [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in answers.items() if k not in known and v and v not in (service,)]
    notes = [f"{'TEST lead from' if test else 'Lead from'} {AdLead.Platform(platform).label} lead form{f' {form_id}' if form_id else ''} (lead id {external_id})."]
    if city:
        notes.append(f"City: {city}")
    notes += other + list(extra_notes)
    with transaction.atomic():
        enquiry = Enquiry.objects.create(
            hospital=hospital, name=(name or "Ad lead")[:255], mobile=mobile or "", email=email[:254], service_requested=service[:255],
            source=Enquiry.Source.META if platform == AdLead.Platform.META else Enquiry.Source.GOOGLE,
            campaign=campaign[:150], utm_source=("facebook" if platform == "meta" else "google") if not medium else medium[:100],
            utm_medium="paid_social" if platform == "meta" else "cpc", utm_campaign=campaign[:150], notes="\n".join(notes),
        )
        return AdLead.objects.create(hospital=hospital, platform=platform, external_id=external_id, form_id=form_id[:80], campaign=campaign[:200],
                                     status=AdLead.Status.TEST if test else AdLead.Status.CREATED, enquiry=enquiry)


def _norm(key):
    return str(key or "").strip().lower().replace(" ", "_").replace("-", "_")


def fetch_meta_lead(integration, leadgen_id, *, form_id=""):
    """Fetch one lead from the Graph API and create its enquiry."""
    try:
        resp = requests.get(f"{GRAPH}/{leadgen_id}", params={"access_token": integration.meta_page_token, "fields": LEAD_FIELDS}, timeout=10)
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            raise ValueError((data.get("error") or {}).get("message") or f"HTTP {resp.status_code}")
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Meta lead %s for hospital %s couldn't be fetched: %s", leadgen_id, integration.hospital_id, exc)
        row, _ = AdLead.objects.update_or_create(
            hospital=integration.hospital, platform=AdLead.Platform.META, external_id=leadgen_id,
            defaults={"status": AdLead.Status.FAILED, "error": str(exc)[:300], "form_id": form_id[:80]},
        )
        return row
    answers = {_norm(f.get("name")): ", ".join(str(v) for v in (f.get("values") or [])) for f in data.get("field_data") or []}
    AdLead.objects.filter(hospital=integration.hospital, platform=AdLead.Platform.META, external_id=leadgen_id, status=AdLead.Status.FAILED).delete()
    medium = "instagram" if data.get("platform") == "ig" else "facebook"
    extra = [f"Ad: {data['ad_name']}"] if data.get("ad_name") else []
    try:
        return create_enquiry(integration.hospital, platform=AdLead.Platform.META, external_id=leadgen_id, answers=answers,
                              campaign=data.get("campaign_name") or "", form_id=data.get("form_id") or form_id, medium=medium, extra_notes=extra)
    except IntegrityError:  # a parallel delivery of the same lead got there first
        return AdLead.objects.get(hospital=integration.hospital, platform=AdLead.Platform.META, external_id=leadgen_id)


class PublicView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []


class MetaLeadWebhookView(PublicView):
    def get(self, request, hospital_id):
        i = _integration(hospital_id)
        q = request.query_params
        if i and q.get("hub.mode") == "subscribe" and hmac.compare_digest(q.get("hub.verify_token", ""), i.meta_verify_token):
            return HttpResponse(q.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponse("Verification failed", status=403)

    def post(self, request, hospital_id):
        i = _integration(hospital_id)
        if i is None or not i.meta_enabled or not i.meta_app_secret:
            return Response({"detail": "Not configured."}, status=404)
        raw = request.body
        expected = "sha256=" + hmac.new(i.meta_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            logger.warning("Rejected a Meta lead webhook with a bad signature for hospital %s", hospital_id)
            return Response({"detail": "Bad signature."}, status=403)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            return Response({"detail": "Bad JSON."}, status=400)
        created = 0
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                leadgen_id = str(value.get("leadgen_id") or "")
                if change.get("field") != "leadgen" or not leadgen_id:
                    continue
                if i.meta_page_id and str(value.get("page_id") or entry.get("id")) != i.meta_page_id:
                    continue  # another page's lead: not this hospital's
                if AdLead.objects.filter(hospital=i.hospital, platform=AdLead.Platform.META, external_id=leadgen_id).exclude(status=AdLead.Status.FAILED).exists():
                    continue  # Meta retried a delivery we already handled
                row = fetch_meta_lead(i, leadgen_id, form_id=str(value.get("form_id") or ""))
                created += row.status == AdLead.Status.CREATED
        return Response({"received": True, "created": created})


class GoogleLeadWebhookView(PublicView):
    def post(self, request, hospital_id):
        i = _integration(hospital_id)
        data = request.data if isinstance(request.data, dict) else {}
        if i is None or not i.google_enabled or not hmac.compare_digest(str(data.get("google_key", "")), i.google_key):
            return Response({"detail": "Invalid key."}, status=403)
        lead_id = str(data.get("lead_id") or "")
        if not lead_id:
            return Response({"lead_id": "Missing."}, status=400)
        answers = {}
        for col in data.get("user_column_data") or []:
            key = _norm(col.get("column_id") or col.get("column_name"))
            answers[key] = str(col.get("string_value") or "")
        campaign = f"Google Ads campaign {data['campaign_id']}" if data.get("campaign_id") else ""
        extra = [f"gclid: {data['gcl_id']}"] if data.get("gcl_id") else []
        try:
            create_enquiry(i.hospital, platform=AdLead.Platform.GOOGLE, external_id=lead_id, answers=answers, campaign=campaign,
                           form_id=str(data.get("form_id") or ""), test=bool(data.get("is_test")), extra_notes=extra)
        except IntegrityError:
            pass  # Google retried a lead we already have
        return Response({}, status=200)


# --- Staff side ---------------------------------------------------------------


def _allowed(user):
    return user.is_superuser or getattr(getattr(user, "role", None), "template", "") in WIDGET_MANAGERS


class AdLeadsAdminView(APIView):
    """GET settings + recent leads; PATCH settings (secrets write-only)."""

    permission_classes = [permissions.IsAuthenticated]

    def _payload(self, request, i):
        base = request.build_absolute_uri("/api/v1/leads/")
        log = AdLead.objects.filter(hospital=i.hospital).select_related("enquiry")[:25]
        return {
            "meta": {"enabled": i.meta_enabled, "webhook_url": f"{base}meta/{i.hospital_id}/", "verify_token": i.meta_verify_token,
                     "page_id": i.meta_page_id, "app_secret_set": bool(i.meta_app_secret), "page_token_set": bool(i.meta_page_token)},
            "google": {"enabled": i.google_enabled, "webhook_url": f"{base}google/{i.hospital_id}/", "key": i.google_key},
            "recent": [{"id": r.pk, "platform": r.platform, "status": r.status, "campaign": r.campaign, "error": r.error,
                        "enquiry": r.enquiry_id, "name": r.enquiry.name if r.enquiry_id else "", "received_at": r.received_at} for r in log],
        }

    def _get_integration(self, request):
        i, _ = AdLeadIntegration.objects.get_or_create(hospital_id=request.user.hospital_id)
        return i

    def get(self, request):
        if not _allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        return Response(self._payload(request, self._get_integration(request)))

    def patch(self, request):
        if not _allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        i = self._get_integration(request)
        d = request.data
        for field in ("meta_app_secret", "meta_page_token"):
            if d.get(field):
                setattr(i, field, str(d[field]).strip())
        if "meta_page_id" in d:
            i.meta_page_id = "".join(ch for ch in str(d["meta_page_id"]) if ch.isdigit())[:40]
        if "meta_enabled" in d:
            if d["meta_enabled"] and not (i.meta_app_secret and i.meta_page_token):
                return Response({"meta_enabled": "Add the app secret and page access token first."}, status=400)
            i.meta_enabled = bool(d["meta_enabled"])
        if "google_enabled" in d:
            i.google_enabled = bool(d["google_enabled"])
        i.save()
        return Response(self._payload(request, i))

    def post(self, request):
        """{retry: <AdLead id>} fetches a failed Meta lead again."""
        if not _allowed(request.user) or not request.user.hospital_id:
            return Response({"detail": "Not allowed."}, status=403)
        i = self._get_integration(request)
        row = AdLead.objects.filter(pk=request.data.get("retry"), hospital_id=request.user.hospital_id, status=AdLead.Status.FAILED).first()
        if row is None:
            return Response({"retry": "No failed lead with that id."}, status=404)
        fetch_meta_lead(i, row.external_id, form_id=row.form_id)
        return Response(self._payload(request, i))
