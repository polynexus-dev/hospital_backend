"""Upload documents from a phone by scanning a QR code.

Staff open "Upload from phone" on a patient: we make a short-lived session
(30 minutes, up to 10 files) and hand back a signed token; the screen shows
it as a QR code. The phone opens /m/upload/<token> (no sign-in), takes a
photo or picks a PDF, and it lands in the patient's documents in the chosen
category, recorded as uploaded by the staff member who made the link.
The phone only ever sees the hospital's name, the patient's first name and
a masked UHID.
"""
from datetime import timedelta

from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from .models import ALLOWED_DOCUMENT_EXTENSIONS, Document, DocumentRequirement, MobileUploadSession, validate_document_size

SALT = "patients.mobile-upload"
DEFAULT_MINUTES = 30


def make_token(session):
    return signing.dumps({"s": session.pk, "h": str(session.hospital_id)}, salt=SALT, compress=True)


def session_for(token):
    try:
        data = signing.loads(token, salt=SALT, max_age=24 * 3600)
    except signing.BadSignature:
        return None
    return MobileUploadSession.objects.filter(pk=data.get("s"), hospital_id=data.get("h")).select_related("patient", "hospital").first()


def start(patient, user, *, category="other", requirement_id=None, minutes=DEFAULT_MINUTES):
    if category not in Document.Category.values:
        raise serializers.ValidationError({"category": f"One of {', '.join(Document.Category.values)}."})
    requirement = None
    if requirement_id:
        requirement = DocumentRequirement.objects.filter(pk=requirement_id, hospital_id=patient.hospital_id).first()
        if requirement is None:
            raise serializers.ValidationError({"requirement": "Not found."})
        category = requirement.category
    minutes = min(max(int(minutes or DEFAULT_MINUTES), 5), 120)
    session = MobileUploadSession.objects.create(hospital_id=patient.hospital_id, patient=patient, category=category, requirement=requirement,
                                                 created_by=user, expires_at=timezone.now() + timedelta(minutes=minutes))
    return session


def describe(session):
    uhid = session.patient.uhid or ""
    return {
        "hospital": session.hospital.name, "patient": session.patient.first_name, "uhid": f"•••{uhid[-4:]}" if uhid else "",
        "category": Document.Category(session.category).label if session.category in Document.Category.values else session.category,
        "requirement": session.requirement.name if session.requirement_id else "",
        "expires_at": session.expires_at, "uploads": session.uploads, "max_files": session.max_files, "open": session.is_open,
    }


class MobileUploadThrottle(AnonRateThrottle):
    rate = "30/minute"


class MobileUploadView(APIView):
    """GET: what this link is for. POST multipart `file` (+ `title`): add it."""

    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [MultiPartParser, FormParser]
    throttle_classes = [MobileUploadThrottle]

    def get(self, request, token):
        session = session_for(token)
        if session is None:
            return Response({"detail": "This link isn't valid. Ask the hospital staff for a new QR code."}, status=404)
        return Response(describe(session))

    def post(self, request, token):
        session = session_for(token)
        if session is None:
            return Response({"detail": "This link isn't valid. Ask the hospital staff for a new QR code."}, status=404)
        if not session.is_open:
            return Response({"detail": "This link has expired or reached its file limit. Ask the staff for a new QR code."}, status=410)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": "Choose a photo or PDF."}, status=400)
        ext = upload.name.rsplit(".", 1)[-1].lower() if "." in upload.name else ""
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            return Response({"file": f"Allowed: {', '.join(sorted(ALLOWED_DOCUMENT_EXTENSIONS))}."}, status=400)
        try:
            validate_document_size(upload)
        except DjangoValidationError as exc:
            return Response({"file": " ".join(exc.messages)}, status=400)
        with transaction.atomic():
            locked = MobileUploadSession.objects.select_for_update().get(pk=session.pk)
            if not locked.is_open:
                return Response({"detail": "This link has reached its file limit."}, status=410)
            title = str(request.data.get("title", "")).strip()[:255] or f"{describe(locked)['category']} (from phone)"
            doc = Document.objects.create(hospital_id=locked.hospital_id, patient=locked.patient, category=locked.category, title=title,
                                          file=upload, uploaded_by=locked.created_by, requirement=locked.requirement)
            MobileUploadSession.objects.filter(pk=locked.pk).update(uploads=F("uploads") + 1)
            locked.refresh_from_db()
        from .models import record_timeline_event

        try:
            record_timeline_event(patient=locked.patient, event_type="document", summary=f"Document uploaded from phone: {title}",
                                  occurred_at=timezone.now(), source=doc, created_by=locked.created_by)
        except Exception:  # noqa: BLE001 - the upload stands
            pass
        return Response({"uploaded": doc.title, **describe(locked)}, status=status.HTTP_201_CREATED)
