from django.core import signing
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, NotFound, ValidationError
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.encryption import blind_index, normalize_phone
from apps.core.models import Hospital
from apps.core.modules import require_module
from apps.patients.models import Patient

from .models import PROM_PREM_INSTRUMENTS, CareInformation, PatientReportedMeasure

TOKEN_SALT = "portal.session"
TOKEN_MAX_AGE = 60 * 60 * 12


class PortalPrincipal:
    is_authenticated = True
    is_staff = False
    is_superuser = False
    can_cross_tenant = False

    def __init__(self, hospital, mobile_hash):
        self.hospital = hospital
        self.hospital_id = hospital.pk
        self.mobile_hash = mobile_hash
        # Distinct per phone number so DRF's per-user throttle buckets
        # don't collapse every portal user into one.
        self.pk = f"portal-{mobile_hash[:24]}"

    def patients(self):
        from django.db.models import Q

        return Patient.objects.filter(hospital=self.hospital).filter(Q(mobile_hash=self.mobile_hash) | Q(alternate_mobile_hash=self.mobile_hash))


class PortalTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Portal "):
            return None
        try:
            data = signing.loads(header[7:], salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
        except signing.BadSignature:
            raise AuthenticationFailed("Portal session expired — please sign in again.")
        hospital = Hospital.objects.filter(pk=data["h"], is_active=True).first()
        if hospital is None:
            raise AuthenticationFailed("Hospital unavailable.")
        return PortalPrincipal(hospital, data["m"]), None


class IsPortalPatient(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.user, PortalPrincipal)


class PortalView(APIView):
    authentication_classes = [PortalTokenAuthentication]
    permission_classes = [IsPortalPatient]

    def patient(self, request):
        pid = request.query_params.get("patient") or request.data.get("patient") if hasattr(request, "data") else None
        qs = request.user.patients()
        p = qs.filter(pk=pid).first() if pid else qs.first()
        if p is None:
            raise NotFound("Patient not found on this account.")
        return p


# --- auth -------------------------------------------------------------------------


class RequestOTPView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        from apps.patients.registration import issue_otp

        hospital = Hospital.objects.filter(slug=str(request.data.get("hospital", "")).lower(), is_active=True).first()
        mobile = str(request.data.get("mobile", "")).strip()
        if hospital is None or not mobile:
            return Response({"detail": "hospital and mobile are required."}, status=400)
        require_module(hospital, "portal")
        # Same response whether or not the number is registered — the
        # portal must not become a lookup oracle for who's a patient.
        if Patient.objects.filter(hospital=hospital).by_mobile(mobile).exists():
            issue_otp(hospital, mobile, purpose="portal")
        return Response({"sent": True})


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        from apps.patients.registration import verify_otp

        hospital = Hospital.objects.filter(slug=str(request.data.get("hospital", "")).lower(), is_active=True).first()
        mobile = str(request.data.get("mobile", "")).strip()
        if hospital is None:
            return Response({"detail": "Unknown hospital."}, status=400)
        require_module(hospital, "portal")
        ok, err = verify_otp(hospital, mobile, request.data.get("otp", ""), purpose="portal")
        if not ok:
            return Response({"otp": err}, status=400)
        mh = blind_index(normalize_phone(mobile))
        token = signing.dumps({"h": str(hospital.pk), "m": mh}, salt=TOKEN_SALT)
        principal = PortalPrincipal(hospital, mh)
        return Response({"token": token, "expires_in": TOKEN_MAX_AGE, "patients": _profiles(principal)})


def _profiles(principal):
    return [{"id": p.pk, "name": p.full_name, "uhid": p.uhid, "date_of_birth": p.date_of_birth, "gender": p.gender} for p in principal.patients()]


# --- data -------------------------------------------------------------------------


class MeView(PortalView):
    def get(self, request):
        return Response({"hospital": request.user.hospital.name, "patients": _profiles(request.user)})


class DoctorsView(PortalView):
    def get(self, request):
        from apps.appointments.models import Doctor

        rows = Doctor.objects.filter(hospital=request.user.hospital, is_active=True).select_related("department").values(
            "id", "name", "speciality", "department__name", "default_consultation_minutes",
        )
        return Response(list(rows))


class SlotsView(PortalView):
    def get(self, request, doctor_id):
        from apps.appointments.models import Slot

        day = request.query_params.get("date") or str(timezone.localdate())
        qs = Slot.objects.filter(hospital=request.user.hospital, doctor_id=doctor_id, date=day, is_blocked=False, appointment__isnull=True).order_by("start_time")
        now = timezone.localtime()
        return Response([{"id": s.pk, "date": s.date, "start_time": s.start_time, "end_time": s.end_time} for s in qs
                         if str(s.date) > str(now.date()) or s.start_time > now.time()])


class AppointmentsView(PortalView):
    def get(self, request):
        from apps.appointments.models import Appointment

        p = self.patient(request)
        rows = Appointment.objects.filter(patient=p).select_related("doctor", "slot").order_by("-slot__date", "-slot__start_time")[:50]
        return Response([{"id": a.pk, "doctor": a.doctor.name, "date": a.slot.date, "time": a.slot.start_time, "status": a.status, "token": a.queue_token} for a in rows])

    def post(self, request):
        """AAC.2.f — the patient books a displayed slot themselves."""
        from apps.appointments.models import Appointment, Slot
        from apps.appointments.services import SlotUnavailable, book_appointment

        p = self.patient(request)
        slot = Slot.objects.filter(pk=request.data.get("slot"), hospital=request.user.hospital).first()
        if slot is None:
            return Response({"slot": "Not found."}, status=404)
        try:
            appt = book_appointment(patient=p, slot=slot, source=Appointment.Source.PATIENT_PORTAL, reason=str(request.data.get("reason", ""))[:255])
        except SlotUnavailable as exc:
            return Response({"detail": str(exc)}, status=409)
        return Response({"id": appt.pk, "status": appt.status, "doctor": appt.doctor.name, "date": slot.date, "time": slot.start_time}, status=201)


class ReportsView(PortalView):
    """AAC.3.l / AAC.4.k — only verified (signed) reports are visible."""

    def get(self, request):
        from apps.laboratory.models import LabOrder
        from apps.radiology.models import RadiologyReport

        p = self.patient(request)
        labs = LabOrder.objects.filter(patient=p, status="verified").order_by("-ordered_at")
        rads = RadiologyReport.objects.filter(radiology_order__patient=p, finalized_at__isnull=False).select_related("radiology_order__procedure").order_by("-finalized_at")
        return Response({
            "laboratory": [{"id": o.pk, "order_number": o.order_number, "date": o.ordered_at, "tests": [t.name for t in o.ordered_tests.all()]} for o in labs],
            "radiology": [{"id": r.pk, "study": r.radiology_order.procedure.name, "date": r.finalized_at, "impression": r.impression, "amended": r.is_amended} for r in rads],
        })


class LabReportPDFView(PortalView):
    def get(self, request, order_id):
        from apps.laboratory.models import LabOrder
        from apps.laboratory.workflow import render_lab_report

        order = LabOrder.objects.filter(pk=order_id, patient__in=request.user.patients(), status="verified").first()
        if order is None:
            raise NotFound()
        return HttpResponse(render_lab_report(order), content_type="application/pdf")


class PrescriptionsView(PortalView):
    """COP.1.l"""

    def get(self, request):
        from apps.patients.models import Prescription

        p = self.patient(request)
        rows = Prescription.objects.filter(patient=p).select_related("doctor").order_by("-created_at")[:50]
        return Response([{"id": r.pk, "date": r.created_at, "doctor": r.doctor.get_full_name() if r.doctor else "", "diagnosis": r.diagnosis, "medications": r.medications} for r in rows])


class PrescriptionPDFView(PortalView):
    def get(self, request, rx_id):
        from apps.patients.models import Prescription
        from apps.patients.prescription_pdf import render_prescription_pdf

        rx = Prescription.objects.filter(pk=rx_id, patient__in=request.user.patients()).first()
        if rx is None:
            raise NotFound()
        return HttpResponse(render_prescription_pdf(rx), content_type="application/pdf")


class DischargeSummariesView(PortalView):
    def get(self, request):
        from apps.ipd.models import DischargeSummary

        p = self.patient(request)
        rows = DischargeSummary.objects.filter(admission__patient=p, finalized_at__isnull=False).select_related("admission").order_by("-created_at")
        return Response([{
            "id": d.pk, "admitted_at": d.admission.admitted_at, "discharged_at": d.admission.discharged_at, "final_diagnosis": d.final_diagnosis,
            "treatment_summary": d.treatment_summary, "discharge_medications": d.discharge_medications, "follow_up_instructions": d.follow_up_instructions,
        } for d in rows])


class BillsView(PortalView):
    """FPM.3.f — bills, payments and outstanding balance."""

    def get(self, request):
        from apps.billing.models import Bill

        p = self.patient(request)
        bills = Bill.objects.filter(patient=p).prefetch_related("items", "payments").order_by("-created_at")
        rows, due = [], 0
        for b in bills:
            paid = sum(float(x.amount) for x in b.payments.all())
            balance = float(b.net_amount) - paid
            due += max(balance, 0)
            rows.append({"id": b.pk, "date": b.created_at, "net_amount": float(b.net_amount), "paid": paid, "balance": round(balance, 2), "status": b.status,
                         "items": [{"description": i.description, "quantity": i.quantity, "amount": float(i.total_price)} for i in b.items.all()]})
        return Response({"outstanding": round(due, 2), "bills": rows})


class TeleconsultationsView(PortalView):
    def get(self, request):
        from apps.telemedicine.models import TeleConsultation
        from apps.telemedicine.views import patient_join_token

        p = self.patient(request)
        rows = TeleConsultation.objects.filter(patient=p).select_related("doctor").order_by("-scheduled_at")[:20]
        return Response([{"id": c.pk, "doctor": c.doctor.name, "scheduled_at": c.scheduled_at, "status": c.status,
                          "join_token": patient_join_token(c) if c.status in ("scheduled", "waiting", "in_progress") else None} for c in rows])


class CareInformationView(PortalView):
    """AAC.7.a"""

    def get(self, request):
        rows = CareInformation.objects.filter(hospital=request.user.hospital, is_published=True).values("id", "category", "title", "body", "language")
        return Response(list(rows))


class ComplaintView(PortalView):
    """AAC.8.a"""

    def post(self, request):
        from apps.feedback.models import Complaint

        p = self.patient(request)
        text = str(request.data.get("description", "")).strip()
        if not text:
            raise ValidationError({"description": "Please describe the issue."})
        c = Complaint.objects.create(hospital=request.user.hospital, patient=p, description=text)
        return Response({"id": c.pk, "status": c.status}, status=201)


class MeasuresView(PortalView):
    """AAC.8.c/d — GET instruments, POST a completed questionnaire."""

    def get(self, request):
        return Response(PROM_PREM_INSTRUMENTS)

    def post(self, request):
        p = self.patient(request)
        key = request.data.get("instrument")
        inst = PROM_PREM_INSTRUMENTS.get(key)
        if inst is None:
            raise ValidationError({"instrument": f"One of {list(PROM_PREM_INSTRUMENTS)}"})
        answers = request.data.get("answers") or {}
        missing = [q["id"] for q in inst["questions"] if q["id"] not in answers]
        if missing:
            raise ValidationError({"answers": f"Missing: {missing}"})
        m = PatientReportedMeasure.objects.create(hospital=request.user.hospital, patient=p, kind=inst["kind"], instrument=inst["title"],
                                                  answers=answers, comments=str(request.data.get("comments", "")))
        return Response({"id": m.pk, "score": m.score}, status=201)


# --- staff side --------------------------------------------------------------------

from apps.core.crud import TenantCRUDViewSet, model_serializer  # noqa: E402


class CareInformationViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(CareInformation)
    queryset = CareInformation.objects.all()
    filterset_fields = ["category", "is_published"]


class PatientReportedMeasureViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(PatientReportedMeasure, extra={"patient_name": serializers.CharField(source="patient.full_name", read_only=True)})
    queryset = PatientReportedMeasure.objects.select_related("patient")
    filterset_fields = ["kind", "patient", "instrument"]
    http_method_names = ["get", "head", "options"]
