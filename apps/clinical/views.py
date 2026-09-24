from django.apps import apps as django_apps
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet
from apps.core.permissions import RequiresClinicalDetailPermission
from apps.patients.models import Patient

from . import services
from .models import (
    Allergy,
    AssessmentTemplate,
    CarePlan,
    ClinicalAlert,
    ClinicalAssessment,
    ConsentRecord,
    DigitalSignature,
    DrugConditionRule,
    DrugInteraction,
    EpisodeOfCare,
    FunctionalAssessment,
    HomecareBooking,
    HomecareService,
    NotifiableDisease,
    NotifiableDiseaseReport,
    OrderSet,
    ResultReview,
    RiskAssessment,
    ShiftHandover,
)
from .scoring import HIGH_RISK_LEVELS
from .serializers import (
    AllergySerializer,
    AssessmentTemplateSerializer,
    CarePlanSerializer,
    ClinicalAlertSerializer,
    ClinicalAssessmentSerializer,
    ConsentRecordSerializer,
    DigitalSignatureSerializer,
    DrugConditionRuleSerializer,
    DrugInteractionSerializer,
    EpisodeOfCareSerializer,
    FunctionalAssessmentSerializer,
    HomecareBookingSerializer,
    HomecareServiceSerializer,
    NotifiableDiseaseReportSerializer,
    NotifiableDiseaseSerializer,
    OrderSetSerializer,
    ResultReviewSerializer,
    RiskAssessmentSerializer,
    ShiftHandoverSerializer,
)
from .signatures import SignatureError, sign_document, signatures_for


class EpisodeOfCareViewSet(ClinicalCRUDViewSet):
    serializer_class = EpisodeOfCareSerializer
    queryset = EpisodeOfCare.objects.select_related("patient")
    filterset_fields = ["patient", "status", "specialty"]
    search_fields = ["episode_code", "condition", "icd_code"]
    audited_fields = ("condition", "status", "ended_on")

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        ep = self.get_object()
        ep.status = EpisodeOfCare.Status.CLOSED
        ep.ended_on = timezone.localdate()
        ep.save(update_fields=["status", "ended_on"])
        return Response(self.get_serializer(ep).data)

    @action(detail=True, methods=["get"])
    def visits(self, request, pk=None):
        ep = self.get_object()
        from apps.ipd.models import Admission
        from apps.opd.models import Encounter

        encounters = Encounter.objects.filter(episode=ep).values("id", "created_at", "doctor__name")
        admissions = Admission.objects.filter(episode=ep).values("id", "admitted_at", "discharged_at", "admission_diagnosis")
        return Response({"episode": self.get_serializer(ep).data, "encounters": list(encounters), "admissions": list(admissions)})


class AllergyViewSet(ClinicalCRUDViewSet):
    serializer_class = AllergySerializer
    queryset = Allergy.objects.select_related("patient")
    filterset_fields = ["patient", "allergen_type", "status", "severity"]
    actor_field = "recorded_by"
    audited_fields = ("allergen", "severity", "status", "reaction")


class AssessmentTemplateViewSet(TenantCRUDViewSet):
    serializer_class = AssessmentTemplateSerializer
    queryset = AssessmentTemplate.objects.all()
    filterset_fields = ["category", "setting", "is_active"]
    audited_fields = ("name", "category", "fields", "is_active")
    action_permissions = {"install_library": "clinical.add_assessmenttemplate"}

    @action(detail=False, methods=["post"], url_path="install-library")
    def install_library(self, request):
        """Adds any pre-built specialty consultation templates this hospital
        doesn't have yet (by name). Never touches existing templates."""
        from .specialty_templates import SPECIALTY_TEMPLATES, install_specialty_library

        added = install_specialty_library(request.user.hospital)
        return Response({"added": added, "library_size": len(SPECIALTY_TEMPLATES)})


class ClinicalAssessmentViewSet(ClinicalCRUDViewSet):
    serializer_class = ClinicalAssessmentSerializer
    queryset = ClinicalAssessment.objects.select_related("patient", "assessed_by")
    filterset_fields = ["patient", "encounter", "admission", "episode", "category", "kind"]
    actor_field = "assessed_by"
    audited_fields = ("kind", "category", "provisional_diagnosis", "data")

    def perform_create(self, serializer):
        super().perform_create(serializer)
        a = serializer.instance
        if a.provisional_diagnosis:
            services.check_notifiable(a.patient, a.provisional_diagnosis)

    @action(detail=False, methods=["get"])
    def progress(self, request):
        """COP.1.a trend view — assessments + vitals over time for one patient."""
        patient_id = request.query_params.get("patient")
        if not patient_id:
            return Response({"detail": "patient is required."}, status=400)
        from apps.opd.models import VitalsReading

        assessments = self.get_queryset().filter(patient_id=patient_id).order_by("assessed_at")
        vitals = VitalsReading.objects.filter(encounter__patient_id=patient_id, hospital_id=request.user.hospital_id).order_by("recorded_at").values(
            "recorded_at", "bp_systolic", "bp_diastolic", "pulse", "temperature_c", "weight_kg",
        )
        risks = RiskAssessment.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id).order_by("assessed_at").values("assessed_at", "tool", "score", "risk_level")
        return Response({
            "assessments": ClinicalAssessmentSerializer(assessments, many=True).data,
            "vitals": list(vitals),
            "risk_scores": list(risks),
        })


class RiskAssessmentViewSet(ClinicalCRUDViewSet):
    serializer_class = RiskAssessmentSerializer
    queryset = RiskAssessment.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "tool", "risk_level"]
    actor_field = "assessed_by"
    audited_fields = ("tool", "score", "risk_level")

    def perform_create(self, serializer):
        super().perform_create(serializer)
        r = serializer.instance
        if r.risk_level in HIGH_RISK_LEVELS:
            alert_type = ClinicalAlert.AlertType.EARLY_WARNING if r.tool == RiskAssessment.Tool.NEWS2 else ClinicalAlert.AlertType.RISK
            services.persist_alerts(r.patient, [{
                "alert_type": alert_type,
                "severity": ClinicalAlert.Severity.CRITICAL if r.risk_level in ("high", "very_high", "highest") else ClinicalAlert.Severity.WARNING,
                "title": f"{r.get_tool_display()}: {r.risk_level.replace('_', ' ')} risk (score {r.score})",
                "message": "Apply the preventive care bundle for this risk and document interventions." + (" Escalate to the treating doctor / rapid response team now." if r.tool == "news2" else ""),
            }], source=r, target_department="nursing")

    @action(detail=False, methods=["get"])
    def high_risk_patients(self, request):
        """COP.9.a — current inpatients whose latest score per tool is high."""
        latest = {}
        for r in self.get_queryset().filter(admission__status="admitted").order_by("assessed_at"):
            latest[(r.patient_id, r.tool)] = r
        rows = [RiskAssessmentSerializer(r).data for r in latest.values() if r.risk_level in HIGH_RISK_LEVELS]
        return Response(rows)


class OrderSetViewSet(TenantCRUDViewSet):
    serializer_class = OrderSetSerializer
    queryset = OrderSet.objects.all()
    filterset_fields = ["kind", "is_active"]
    search_fields = ["name"]
    actor_field = "created_by"
    audited_fields = ("name", "items", "diagnosis_codes", "is_active")

    @action(detail=False, methods=["get"])
    def suggest(self, request):
        """COP.1.i — order sets matching a diagnosis (ICD code and/or text)."""
        icd = (request.query_params.get("icd") or "").strip().upper()
        text = (request.query_params.get("diagnosis") or "").strip().lower()
        out = []
        for s in self.get_queryset().filter(is_active=True):
            if (icd and any(icd.startswith(c.upper()) for c in s.diagnosis_codes)) or (text and any(k.lower() in text for k in s.diagnosis_keywords)):
                out.append(s)
        return Response(OrderSetSerializer(out, many=True).data)

    @action(detail=False, methods=["get"])
    def frequent_medications(self, request):
        """COP.1.c — the doctor's (or hospital's) most prescribed medicines,
        as a starting point for building a medication order set."""
        from collections import Counter

        from apps.patients.models import Prescription

        qs = Prescription.objects.filter(hospital_id=request.user.hospital_id)
        if request.query_params.get("mine") == "1":
            qs = qs.filter(doctor=request.user)
        counter = Counter()
        sample = {}
        for rx in qs.order_by("-created_at")[:2000]:
            for m in rx.medications or []:
                name = services.med_name(m)
                if name:
                    counter[name] += 1
                    sample.setdefault(name, m if isinstance(m, dict) else {"name": name})
        return Response([{"name": n, "count": c, "example": sample[n]} for n, c in counter.most_common(30)])


class ConsentRecordViewSet(ClinicalCRUDViewSet):
    serializer_class = ConsentRecordSerializer
    queryset = ConsentRecord.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "surgery_request", "consent_type", "status"]
    actor_field = "obtained_by"
    audited_fields = ("consent_type", "status", "given_by", "guardian_name")

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        c = self.get_object()
        c.status = ConsentRecord.Status.WITHDRAWN
        c.withdrawn_at = timezone.now()
        c.withdrawal_reason = str(request.data.get("reason", ""))[:255]
        c.save(update_fields=["status", "withdrawn_at", "withdrawal_reason"])
        return Response(self.get_serializer(c).data)


class ShiftHandoverViewSet(ClinicalCRUDViewSet):
    serializer_class = ShiftHandoverSerializer
    queryset = ShiftHandover.objects.select_related("admission__patient", "handed_over_by", "handed_over_to")
    filterset_fields = ["admission", "shift", "handover_role", "handed_over_to"]
    actor_field = "handed_over_by"
    audited_fields = ("situation", "assessment", "recommendation")

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        h = self.get_object()
        if h.handed_over_to_id and h.handed_over_to_id != request.user.pk:
            return Response({"detail": "Only the receiving clinician can acknowledge this handover."}, status=403)
        h.handed_over_to = request.user
        h.acknowledged_at = timezone.now()
        h.save(update_fields=["handed_over_to", "acknowledged_at"])
        return Response(self.get_serializer(h).data)


class CarePlanViewSet(ClinicalCRUDViewSet):
    serializer_class = CarePlanSerializer
    queryset = CarePlan.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "status"]
    actor_field = "created_by"
    audited_fields = ("title", "status", "goals", "interventions", "review_date")


class DrugInteractionViewSet(TenantCRUDViewSet):
    serializer_class = DrugInteractionSerializer
    queryset = DrugInteraction.objects.all()
    filterset_fields = ["severity"]
    search_fields = ["drug_a", "drug_b"]
    audited_fields = ("drug_a", "drug_b", "severity")


class DrugConditionRuleViewSet(TenantCRUDViewSet):
    serializer_class = DrugConditionRuleSerializer
    queryset = DrugConditionRule.objects.all()
    filterset_fields = ["applies_to", "severity"]
    search_fields = ["drug"]
    audited_fields = ("drug", "condition_keywords", "severity")


class ClinicalAlertViewSet(ClinicalCRUDViewSet):
    serializer_class = ClinicalAlertSerializer
    queryset = ClinicalAlert.objects.select_related("patient", "acknowledged_by")
    filterset_fields = ["patient", "alert_type", "severity", "target_user", "target_department"]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get("open") == "1":
            qs = qs.filter(acknowledged_at__isnull=True)
        if self.request.query_params.get("mine") == "1":
            qs = qs.filter(Q(target_user=self.request.user) | Q(target_user__isnull=True))
        return qs

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        if alert.severity == ClinicalAlert.Severity.CRITICAL and alert.alert_type in (
            ClinicalAlert.AlertType.ALLERGY, ClinicalAlert.AlertType.INTERACTION, ClinicalAlert.AlertType.CONTRAINDICATION,
        ) and not request.data.get("override_reason"):
            return Response({"override_reason": "A reason is required to override a critical safety alert."}, status=400)
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.override_reason = str(request.data.get("override_reason", ""))[:255]
        alert.save(update_fields=["acknowledged_by", "acknowledged_at", "override_reason"])
        return Response(self.get_serializer(alert).data)

    @action(detail=False, methods=["get"])
    def counts(self, request):
        qs = self.get_queryset().filter(acknowledged_at__isnull=True).filter(Q(target_user=request.user) | Q(target_user__isnull=True))
        return Response(dict(qs.values_list("severity").annotate(n=Count("id"))))


class CDSSCheckView(APIView):
    """COP.12.a/b, MOM.2.c/g — run before saving a prescription/order.
    POST {patient, medications:[{name,...}], persist?: bool}."""

    permission_classes = [IsAuthenticated, RequiresClinicalDetailPermission]

    def post(self, request):
        patient = Patient.objects.filter(pk=request.data.get("patient"), hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"patient": "Not found."}, status=404)
        alerts = services.check_medications(patient, request.data.get("medications") or [])
        if request.data.get("modality"):
            alerts += services.check_radiology_contraindications(patient, request.data["modality"], request.data.get("procedure_name", ""))
        if request.data.get("persist"):
            services.persist_alerts(patient, alerts, target_user=request.user)
        return Response({"alerts": alerts, "blocking": any(a["severity"] == "critical" for a in alerts)})


class NotifiableDiseaseViewSet(TenantCRUDViewSet):
    serializer_class = NotifiableDiseaseSerializer
    queryset = NotifiableDisease.objects.all()
    search_fields = ["name"]
    audited_fields = ("name", "icd_codes", "is_active")


class NotifiableDiseaseReportViewSet(ClinicalCRUDViewSet):
    serializer_class = NotifiableDiseaseReportSerializer
    queryset = NotifiableDiseaseReport.objects.select_related("patient", "disease")
    filterset_fields = ["status", "disease", "patient"]
    audited_fields = ("status", "reference_number")

    @action(detail=True, methods=["post"])
    def mark_reported(self, request, pk=None):
        r = self.get_object()
        r.status = NotifiableDiseaseReport.Status.REPORTED
        r.reported_at = timezone.now()
        r.reported_by = request.user
        r.reference_number = str(request.data.get("reference_number", ""))[:80]
        r.save(update_fields=["status", "reported_at", "reported_by", "reference_number"])
        ClinicalAlert.objects.filter(object_id=str(r.pk), alert_type=ClinicalAlert.AlertType.NOTIFIABLE_DISEASE, acknowledged_at__isnull=True).update(
            acknowledged_by=request.user, acknowledged_at=timezone.now(),
        )
        return Response(self.get_serializer(r).data)


class ResultReviewViewSet(ClinicalCRUDViewSet):
    serializer_class = ResultReviewSerializer
    queryset = ResultReview.objects.select_related("patient", "reviewed_by")
    filterset_fields = ["patient", "source_type"]
    actor_field = "reviewed_by"


class HomecareServiceViewSet(TenantCRUDViewSet):
    serializer_class = HomecareServiceSerializer
    queryset = HomecareService.objects.all()
    filterset_fields = ["kind", "is_active"]


class HomecareBookingViewSet(ClinicalCRUDViewSet):
    serializer_class = HomecareBookingSerializer
    queryset = HomecareBooking.objects.select_related("patient", "service", "assigned_staff")
    filterset_fields = ["patient", "status", "assigned_staff", "service"]
    audited_fields = ("status", "assigned_staff", "scheduled_at")

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """Marks the visit done and raises the bill for it (COP.10.b)."""
        from apps.billing.models import Bill, BillItem

        b = self.get_object()
        b.status = HomecareBooking.Status.COMPLETED
        b.completed_at = timezone.now()
        b.visit_notes = request.data.get("visit_notes", b.visit_notes)
        b.vitals = request.data.get("vitals", b.vitals) or {}
        if b.bill_id is None and b.service.price:
            bill = Bill.objects.create(hospital_id=b.hospital_id, patient=b.patient, total_amount=b.service.price, net_amount=b.service.price)
            BillItem.objects.create(bill=bill, description=f"Homecare: {b.service.name}", quantity=1, unit_price=b.service.price, total_price=b.service.price)
            b.bill = bill
        b.save()
        return Response(self.get_serializer(b).data)

    @action(detail=True, methods=["post"])
    def feedback(self, request, pk=None):
        b = self.get_object()
        try:
            score = int(request.data.get("score"))
        except (TypeError, ValueError):
            return Response({"score": "1–5 required."}, status=400)
        if not 1 <= score <= 5:
            return Response({"score": "1–5 required."}, status=400)
        b.feedback_score = score
        b.feedback_comment = str(request.data.get("comment", ""))
        b.save(update_fields=["feedback_score", "feedback_comment"])
        return Response(self.get_serializer(b).data)


class FunctionalAssessmentViewSet(ClinicalCRUDViewSet):
    serializer_class = FunctionalAssessmentSerializer
    queryset = FunctionalAssessment.objects.select_related("patient", "previous")
    filterset_fields = ["patient", "discipline", "scale"]
    actor_field = "assessed_by"
    audited_fields = ("scale", "scores", "total")


# --- Digital signatures (COP.1.e) ------------------------------------------

SIGNABLE = {
    "prescription": "patients.Prescription",
    "discharge_summary": "ipd.DischargeSummary",
    "lab_order": "laboratory.LabOrder",
    "radiology_report": "radiology.RadiologyReport",
    "clinical_note": "opd.ClinicalNote",
    "progress_note": "ipd.DoctorProgressNote",
    "operative_note": "ot.OperativeNote",
    "assessment": "clinical.ClinicalAssessment",
    "consent": "clinical.ConsentRecord",
    "care_plan": "clinical.CarePlan",
}


class SignDocumentView(APIView):
    """POST {document_type, document_id, method: password|totp|stylus,
    password?, otp?} (+ multipart signature_image for stylus)."""

    permission_classes = [IsAuthenticated, RequiresClinicalDetailPermission]

    def _resolve(self, request):
        label = SIGNABLE.get(request.data.get("document_type") or request.query_params.get("document_type"))
        if not label:
            return None
        model = django_apps.get_model(label)
        pk = request.data.get("document_id") or request.query_params.get("document_id")
        return model._base_manager.filter(pk=pk, hospital_id=request.user.hospital_id).first()

    def get(self, request):
        doc = self._resolve(request)
        if doc is None:
            return Response({"detail": "Document not found."}, status=404)
        return Response(DigitalSignatureSerializer(signatures_for(doc), many=True).data)

    def post(self, request):
        doc = self._resolve(request)
        if doc is None:
            return Response({"detail": "Document not found."}, status=404)
        try:
            sig = sign_document(
                doc, request.user, method=request.data.get("method", DigitalSignature.Method.PASSWORD),
                password=request.data.get("password", ""), otp=request.data.get("otp", ""),
                signature_image=request.FILES.get("signature_image"),
            )
        except SignatureError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(DigitalSignatureSerializer(sig).data, status=status.HTTP_201_CREATED)


class PatientClinicalSummaryView(APIView):
    """AAC.1.i / COP.1.n / COP.1.d — every record linked to one UHID across
    OPD, IPD, lab, radiology, pharmacy, blood bank, OT and this app, for the
    treating clinician's single-screen review of past records."""

    permission_classes = [IsAuthenticated, RequiresClinicalDetailPermission]

    def get(self, request, patient_id):
        patient = Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"detail": "Not found."}, status=404)
        return self.build(patient)

    def build(self, patient):
        from apps.ipd.models import Admission, DischargeSummary
        from apps.laboratory.models import LabResult
        from apps.opd.models import Diagnosis, Encounter, VitalsReading
        from apps.patients.models import Prescription
        from apps.radiology.models import RadiologyOrder

        def rows(qs, *fields):
            return list(qs.values(*fields)[:50])

        return Response({
            "patient": {"id": patient.pk, "uhid": patient.uhid, "name": patient.full_name, "dob": patient.date_of_birth, "gender": patient.gender, "blood_group": patient.blood_group},
            "allergies": AllergySerializer(patient.allergies.filter(status="active"), many=True).data,
            "active_alerts": ClinicalAlertSerializer(patient.clinical_alerts.filter(acknowledged_at__isnull=True)[:20], many=True).data,
            "episodes": EpisodeOfCareSerializer(patient.episodes.all(), many=True).data,
            "encounters": rows(Encounter.objects.filter(patient=patient).order_by("-created_at"), "id", "created_at", "doctor__name", "department__name"),
            "diagnoses": rows(Diagnosis.objects.filter(encounter__patient=patient).order_by("-created_at"), "id", "icd_code", "description", "diagnosis_type", "created_at"),
            "vitals": rows(VitalsReading.objects.filter(encounter__patient=patient).order_by("-recorded_at"), "recorded_at", "bp_systolic", "bp_diastolic", "pulse", "temperature_c", "weight_kg", "height_cm"),
            "prescriptions": rows(Prescription.objects.filter(patient=patient).order_by("-created_at"), "id", "created_at", "medications"),
            "lab_results": rows(LabResult.objects.filter(lab_order__patient=patient).order_by("-created_at"), "id", "lab_test__name", "value", "unit", "reference_range", "flag", "created_at"),
            "radiology": rows(RadiologyOrder.objects.filter(patient=patient).order_by("-ordered_at"), "id", "procedure__name", "procedure__modality", "status", "ordered_at", "report__impression"),
            "admissions": rows(Admission.objects.filter(patient=patient).order_by("-admitted_at"), "id", "admitted_at", "discharged_at", "admission_diagnosis", "status"),
            "discharge_summaries": rows(DischargeSummary.objects.filter(admission__patient=patient).order_by("-created_at"), "id", "admission_id", "final_diagnosis", "created_at"),
            "assessments": ClinicalAssessmentSerializer(patient.assessments.all()[:20], many=True).data,
            "risk_assessments": RiskAssessmentSerializer(patient.risk_assessments.all()[:20], many=True).data,
            "care_plans": CarePlanSerializer(patient.care_plans.filter(status="active"), many=True).data,
            "consents": ConsentRecordSerializer(patient.consents.all()[:20], many=True).data,
            "reviews": ResultReviewSerializer(patient.result_reviews.all()[:20], many=True).data,
        })
