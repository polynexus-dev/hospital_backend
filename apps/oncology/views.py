import random
from datetime import timedelta

from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from .models import (
    BoneMarrowTransplant,
    CancerCase,
    ChemoCycle,
    ChemoProtocol,
    ClinicalTrial,
    RadiotherapyFraction,
    RadiotherapyPlan,
    SurgicalOncologyRecord,
    TrialEnrollment,
    TumorBoardAttendance,
    TumorBoardCase,
    TumorBoardMeeting,
)

_patient = {
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
}


class CancerCaseSerializer(model_serializer(CancerCase, read_only=("death_within_30_days_of_treatment",), extra=_patient)):
    tnm = serializers.CharField(read_only=True)


class CancerCaseViewSet(ClinicalCRUDViewSet):
    serializer_class = CancerCaseSerializer
    queryset = CancerCase.objects.select_related("patient")
    filterset_fields = ["patient", "status", "primary_site", "treatment_intent"]
    search_fields = ["primary_site", "icd10_code", "icdo3_topography", "histology"]
    audited_fields = ("t_stage", "n_stage", "m_stage", "stage_group", "status")

    @action(detail=True, methods=["post"])
    def record_death(self, request, pk=None):
        """COP.1.a oncology — cause of death; flags death within 30 days of
        the last treatment and the modality involved."""
        from datetime import date

        case = self.get_object()
        try:
            dod = date.fromisoformat(request.data["date_of_death"])
        except (KeyError, ValueError):
            return Response({"date_of_death": "YYYY-MM-DD required."}, status=400)
        case.date_of_death = dod
        case.cause_of_death = str(request.data.get("cause_of_death", ""))[:255]
        case.status = CancerCase.Status.DECEASED
        last = case.last_treatment_date()
        case.death_within_30_days_of_treatment = bool(last and (dod - last).days <= 30)
        case.death_related_modality = str(request.data.get("modality", ""))[:20] if case.death_within_30_days_of_treatment else ""
        case.save()
        return Response(self.get_serializer(case).data)

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        """Single-screen case view used by the tumour board (MDC.1.b)."""
        case = self.get_object()
        from apps.laboratory.models import LabResult
        from apps.radiology.models import RadiologyReport

        return Response({
            "case": self.get_serializer(case).data,
            "surgeries": list(case.surgeries.values()),
            "chemo": list(case.chemo_cycles.values("cycle_number", "protocol__name", "indication", "given_on", "status", "toxicities", "response")),
            "radiotherapy": list(case.radiotherapy_plans.values("site", "technique", "total_dose_gy", "fractions", "start_date", "end_date")),
            "recent_labs": list(LabResult.objects.filter(lab_order__patient=case.patient).order_by("-created_at").values("lab_test__name", "value", "unit", "flag", "created_at")[:20]),
            "radiology": list(RadiologyReport.objects.filter(radiology_order__patient=case.patient).order_by("-created_at").values("radiology_order__procedure__name", "impression", "created_at")[:10]),
            "board_reviews": list(case.board_reviews.values("meeting__board_id", "meeting__scheduled_at", "recommendation", "treatment_plan")),
        })

    @action(detail=False, methods=["get"])
    def registry(self, request):
        """Hospital cancer registry counts (ICD-O-3 topography × stage)."""
        qs = self.get_queryset()
        return Response({
            "by_site": list(qs.values("primary_site").annotate(n=Count("id")).order_by("-n")[:20]),
            "by_stage": list(qs.values("stage_group").annotate(n=Count("id")).order_by("stage_group")),
            "deaths_within_30_days": qs.filter(death_within_30_days_of_treatment=True).count(),
        })


class SurgicalOncologyViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(SurgicalOncologyRecord)
    queryset = SurgicalOncologyRecord.objects.all()
    filterset_fields = ["case"]


class ChemoProtocolViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ChemoProtocol)
    queryset = ChemoProtocol.objects.all()
    search_fields = ["name", "indication"]


class ChemoCycleViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(ChemoCycle, read_only=("bsa", "calculated_doses", "prescribed_by", "verified_by"), extra={
        "protocol_name": serializers.CharField(source="protocol.name", read_only=True),
        "patient_name": serializers.CharField(source="case.patient.full_name", read_only=True),
    })
    queryset = ChemoCycle.objects.select_related("protocol", "case__patient")
    filterset_fields = ["case", "status", "planned_on"]
    actor_field = "prescribed_by"
    audited_fields = ("status", "dose_reduction_pct", "given_on")

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        c = self.get_object()
        if c.prescribed_by_id == request.user.pk:
            return Response({"detail": "Chemotherapy doses must be verified by a second clinician/pharmacist."}, status=403)
        c.verified_by = request.user
        c.save(update_fields=["verified_by"])
        return Response(self.get_serializer(c).data)

    @action(detail=True, methods=["post"])
    def administer(self, request, pk=None):
        c = self.get_object()
        if not c.verified_by_id:
            return Response({"detail": "Doses not yet independently verified."}, status=400)
        labs = c.pre_chemo_labs or {}
        if not request.data.get("override") and (float(labs.get("ANC", 99)) < 1.5 or float(labs.get("platelets", 999)) < 100):
            return Response({"detail": "Pre-chemo counts below threshold (ANC ≥ 1.5, platelets ≥ 100) — delay or override with reason.", "code": "counts_low"}, status=400)
        c.status = ChemoCycle.Status.GIVEN
        c.given_on = timezone.localdate()
        c.save()
        if c.case.status == CancerCase.Status.WORKUP:
            c.case.status = CancerCase.Status.ON_TREATMENT
            c.case.save(update_fields=["status"])
        return Response(self.get_serializer(c).data)

    @action(detail=False, methods=["get"])
    def daycare_schedule(self, request):
        day = request.query_params.get("date") or str(timezone.localdate())
        return Response(self.get_serializer(self.get_queryset().filter(planned_on=day), many=True).data)


class RadiotherapyPlanSerializer(model_serializer(RadiotherapyPlan)):
    dose_per_fraction = serializers.FloatField(read_only=True)
    fractions_delivered = serializers.SerializerMethodField()

    def get_fractions_delivered(self, obj):
        return obj.delivered_fractions.count()


class RadiotherapyPlanViewSet(ClinicalCRUDViewSet):
    serializer_class = RadiotherapyPlanSerializer
    queryset = RadiotherapyPlan.objects.all()
    filterset_fields = ["case"]

    @action(detail=True, methods=["post"])
    def deliver_fraction(self, request, pk=None):
        plan = self.get_object()
        n = plan.delivered_fractions.count() + 1
        if n > plan.fractions:
            return Response({"detail": "All planned fractions already delivered."}, status=400)
        RadiotherapyFraction.objects.create(hospital_id=plan.hospital_id, plan=plan, fraction_number=n, dose_gy=request.data.get("dose_gy") or plan.dose_per_fraction,
                                            remarks=str(request.data.get("remarks", ""))[:255])
        if plan.start_date is None:
            plan.start_date = timezone.localdate()
        if n == plan.fractions:
            plan.end_date = timezone.localdate()
        plan.save()
        return Response(self.get_serializer(plan).data)


class TumorBoardMeetingSerializer(model_serializer(TumorBoardMeeting, read_only=("board_id", "created_by"))):
    patients = serializers.SerializerMethodField()
    attendance = serializers.SerializerMethodField()

    def get_patients(self, obj):
        return [{"case_id": c.case_id, "patient": c.case.patient.full_name, "uhid": c.case.patient.uhid, "site": c.case.primary_site,
                 "tnm": c.case.tnm, "stage": c.case.stage_group, "recommendation": c.recommendation} for c in obj.cases.select_related("case__patient")]

    def get_attendance(self, obj):
        return [{"member": a.member.get_full_name(), "designation": a.designation, "specialty": a.specialty, "role": a.role, "attended": a.attended} for a in obj.attendance.select_related("member")]


class TumorBoardMeetingViewSet(ClinicalCRUDViewSet):
    serializer_class = TumorBoardMeetingSerializer
    queryset = TumorBoardMeeting.objects.prefetch_related("members")
    filterset_fields = ["status"]
    actor_field = "created_by"

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._notify(serializer.instance, "scheduled")

    def _notify(self, meeting, what):
        from apps.clinical.models import ClinicalAlert

        for m in meeting.members.all():
            ClinicalAlert.objects.create(
                hospital_id=meeting.hospital_id, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.INFO, target_user=m,
                title=f"{meeting.board_id} {what} — {timezone.localtime(meeting.scheduled_at):%d %b %H:%M}",
                message=f"{meeting.title} at {meeting.location or 'TBD'}. {meeting.cases.count()} case(s).", object_id=str(meeting.pk),
            )

    @action(detail=True, methods=["post"])
    def add_case(self, request, pk=None):
        meeting = self.get_object()
        case = CancerCase.objects.filter(pk=request.data.get("case"), hospital_id=meeting.hospital_id).first()
        if case is None:
            return Response({"case": "Not found."}, status=404)
        TumorBoardCase.objects.get_or_create(hospital_id=meeting.hospital_id, meeting=meeting, case=case,
                                             defaults={"presented_by": request.user, "question": str(request.data.get("question", ""))})
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"])
    def record_attendance(self, request, pk=None):
        meeting = self.get_object()
        for row in request.data.get("attendees") or []:
            TumorBoardAttendance.objects.update_or_create(
                hospital_id=meeting.hospital_id, meeting=meeting, member_id=row["member"],
                defaults={k: row.get(k, "") for k in ("designation", "specialty", "role")} | {"attended": bool(row.get("attended", True))},
            )
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"])
    def record_decision(self, request, pk=None):
        meeting = self.get_object()
        tb = meeting.cases.filter(case_id=request.data.get("case")).first()
        if tb is None:
            return Response({"case": "Case isn't on this board."}, status=404)
        for f in ("discussion_summary", "recommendation", "treatment_plan", "follow_up"):
            if f in request.data:
                setattr(tb, f, request.data[f])
        tb.is_consensus = bool(request.data.get("is_consensus", True))
        tb.save()
        meeting.status = "held"
        meeting.save(update_fields=["status"])
        return Response(self.get_serializer(meeting).data)

    @action(detail=True, methods=["post"])
    def remind(self, request, pk=None):
        meeting = self.get_object()
        self._notify(meeting, "reminder")
        return Response({"sent": meeting.members.count()})


class ClinicalTrialViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ClinicalTrial)
    queryset = ClinicalTrial.objects.all()
    filterset_fields = ["status"]
    search_fields = ["trial_id", "title"]


class TrialEnrollmentViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(TrialEnrollment, read_only=("subject_id", "arm"), extra={
        "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
        "trial_code": serializers.CharField(source="trial.trial_id", read_only=True),
    })
    queryset = TrialEnrollment.objects.select_related("patient", "trial")
    filterset_fields = ["trial", "patient", "status"]

    def perform_create(self, serializer):
        consent = serializer.validated_data["consent"]
        if consent.consent_type != "research" or consent.status != "granted" or consent.patient_id != serializer.validated_data["patient"].pk:
            raise ValidationError({"consent": "A granted research consent for this patient is required."})
        trial = serializer.validated_data["trial"]
        with transaction.atomic():
            n = TrialEnrollment.objects.select_for_update().filter(trial=trial).count() + 1
            serializer.validated_data["subject_id"] = f"{trial.trial_id}-{n:04d}"
            serializer.validated_data["arm"] = random.SystemRandom().choice(trial.arms) if trial.arms else ""
            super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def adverse_event(self, request, pk=None):
        e = self.get_object()
        ev = {"event": request.data.get("event", ""), "ctcae_grade": request.data.get("ctcae_grade"), "serious": bool(request.data.get("serious")), "date": str(timezone.localdate())}
        e.adverse_events = [*e.adverse_events, ev]
        e.save(update_fields=["adverse_events"])
        if ev["serious"]:
            from apps.clinical.models import ClinicalAlert

            ClinicalAlert.objects.create(hospital_id=e.hospital_id, patient=e.patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.CRITICAL,
                                         target_user=e.trial.principal_investigator, title=f"SAE in {e.trial.trial_id}", message=f"{ev['event']} (grade {ev['ctcae_grade']}) — report to ethics committee within 24 h.")
        return Response(self.get_serializer(e).data)

    @action(detail=True, methods=["post"])
    def dispense_ip(self, request, pk=None):
        e = self.get_object()
        qty = int(request.data.get("quantity", 1))
        trial = e.trial
        if trial.ip_stock < qty:
            return Response({"detail": "Insufficient investigational product stock."}, status=409)
        trial.ip_stock -= qty
        trial.save(update_fields=["ip_stock"])
        e.ip_dispensed += qty
        e.save(update_fields=["ip_dispensed"])
        return Response(self.get_serializer(e).data)


class BoneMarrowTransplantViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(BoneMarrowTransplant, extra={"recipient_name": serializers.CharField(source="recipient.full_name", read_only=True)})
    queryset = BoneMarrowTransplant.objects.select_related("recipient", "donor")
    filterset_fields = ["kind", "status", "recipient"]

    def perform_create(self, serializer):
        v = serializer.validated_data
        if v["kind"] == BoneMarrowTransplant.Kind.ALLOGENEIC and not v.get("donor"):
            raise ValidationError({"donor": "Allogeneic transplant needs a donor record."})
        if v["kind"] == BoneMarrowTransplant.Kind.AUTOLOGOUS:
            v["donor"] = v["recipient"]
            v["donor_relation"] = "self"
        super().perform_create(serializer)


DEFAULT_PROTOCOLS = [
    ("AC (breast)", "Breast cancer", [{"drug": "Doxorubicin", "dose": 60, "unit": "mg/m2", "route": "IV", "day": 1}, {"drug": "Cyclophosphamide", "dose": 600, "unit": "mg/m2", "route": "IV", "day": 1}], 21, 4, "high"),
    ("Paclitaxel weekly", "Breast cancer", [{"drug": "Paclitaxel", "dose": 80, "unit": "mg/m2", "route": "IV", "day": 1}], 7, 12, "low"),
    ("Cisplatin weekly (CRT)", "Head & neck / cervix with RT", [{"drug": "Cisplatin", "dose": 40, "unit": "mg/m2", "route": "IV", "day": 1}], 7, 6, "high"),
    ("CAPOX", "Colorectal", [{"drug": "Oxaliplatin", "dose": 130, "unit": "mg/m2", "route": "IV", "day": 1}, {"drug": "Capecitabine", "dose": 1000, "unit": "mg/m2", "route": "PO BD", "day": "1-14"}], 21, 8, "moderate"),
    ("R-CHOP", "Diffuse large B-cell lymphoma", [{"drug": "Rituximab", "dose": 375, "unit": "mg/m2", "route": "IV", "day": 1}, {"drug": "Cyclophosphamide", "dose": 750, "unit": "mg/m2", "route": "IV", "day": 1}, {"drug": "Doxorubicin", "dose": 50, "unit": "mg/m2", "route": "IV", "day": 1}, {"drug": "Vincristine", "dose": 1.4, "unit": "mg/m2", "route": "IV", "day": 1}], 21, 6, "high"),
]


def seed_protocols(hospital, get_model=None):
    model = get_model("oncology", "ChemoProtocol") if get_model else ChemoProtocol
    if not model.objects.filter(hospital=hospital).exists():
        model.objects.bulk_create([model(hospital=hospital, name=n, indication=i, drugs=d, cycle_length_days=cl, planned_cycles=pc, emetogenic_risk=e) for n, i, d, cl, pc, e in DEFAULT_PROTOCOLS])
