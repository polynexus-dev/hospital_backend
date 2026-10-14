"""EMR additions (MedNet M2.22 parity): risk-tool forms and intervention
checklists, structured patient history, and the patient header cards."""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer
from apps.core.crud import CLINICAL_PERMISSION_CLASSES

from . import scoring
from .models import InterventionBundle, PatientHistory, RiskAssessment


def interventions_for(hospital_id, tool, level):
    bundle = InterventionBundle.objects.filter(hospital_id=hospital_id, tool=tool, risk_level=level, is_active=True).first()
    if bundle is not None:
        return list(bundle.items)
    return list(scoring.DEFAULT_INTERVENTIONS.get((tool, level), []))


def tools_payload(hospital_id):
    """Every tool's questions, its risk levels and each level's checklist."""
    out = []
    for value, label in RiskAssessment.Tool.choices:
        out.append({
            "tool": value, "label": label, "items": scoring.RISK_FORMS.get(value, []),
            "levels": [{"level": lv, "high_risk": lv in scoring.HIGH_RISK_LEVELS, "interventions": interventions_for(hospital_id, value, lv)}
                       for lv in scoring.LEVELS.get(value, [])],
        })
    return out


class RiskInterventionsMixin:
    """Added to RiskAssessmentViewSet."""

    def perform_create(self, serializer):
        super().perform_create(serializer)
        r = serializer.instance
        items = interventions_for(r.hospital_id, r.tool, r.risk_level)
        if items:
            RiskAssessment.objects.filter(pk=r.pk).update(intervention_items=items)
            r.intervention_items = items

    @action(detail=False, methods=["get"])
    def tools(self, request):
        return Response(tools_payload(request.user.hospital_id))

    @action(detail=True, methods=["post"])
    def interventions(self, request, pk=None):
        """POST {done: [item, ...], undone?: [item, ...]} — tick (or untick) checklist items."""
        r = self.get_object()
        done = dict(r.interventions_done or {})
        who = request.user.get_full_name() or request.user.email
        for item in request.data.get("done") or []:
            if item in r.intervention_items and item not in done:
                done[item] = {"by": who, "at": timezone.now().isoformat()}
        for item in request.data.get("undone") or []:
            done.pop(item, None)
        RiskAssessment.objects.filter(pk=r.pk).update(interventions_done=done)
        r.interventions_done = done
        self._log("update", r)
        return Response(self.get_serializer(r).data)

    @action(detail=False, methods=["get"], url_path="intervention-compliance")
    def intervention_compliance(self, request):
        """GET ?hours=4 — high-risk scores older than `hours` with checklist items still open (admitted patients)."""
        try:
            hours = max(int(request.query_params.get("hours", 4)), 0)
        except ValueError:
            hours = 4
        cutoff = timezone.now() - timedelta(hours=hours)
        latest = {}
        for r in self.get_queryset().filter(admission__status="admitted").order_by("assessed_at"):
            latest[(r.patient_id, r.tool)] = r
        rows = []
        for r in latest.values():
            if r.risk_level in scoring.HIGH_RISK_LEVELS and r.assessed_at <= cutoff and r.interventions_pending:
                rows.append({**self.get_serializer(r).data, "pending": r.interventions_pending})
        return Response(rows)


class InterventionBundleViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(InterventionBundle)
    queryset = InterventionBundle.objects.all()
    filterset_fields = ["tool", "risk_level", "is_active"]
    audited_fields = ("tool", "risk_level", "items", "is_active")


# --- history and header --------------------------------------------------------------

def _list_of_dicts(value, name, required_key):
    if not isinstance(value, list) or not all(isinstance(x, dict) and str(x.get(required_key, "")).strip() for x in value):
        raise serializers.ValidationError({name: f"A list of entries, each with a {required_key}."})
    return [{k: v for k, v in x.items() if v not in (None, "")} for x in value]


class PatientHistorySerializer(serializers.ModelSerializer):
    updated_by_name = serializers.CharField(source="updated_by.get_full_name", read_only=True, default="")

    class Meta:
        model = PatientHistory
        fields = ["past_medical", "surgical", "family", "habits", "obstetric", "notes", "updated_at", "updated_by_name"]

    def validate_past_medical(self, v):
        return _list_of_dicts(v, "past_medical", "condition")

    def validate_surgical(self, v):
        return _list_of_dicts(v, "surgical", "procedure")

    def validate_family(self, v):
        return _list_of_dicts(v, "family", "condition")

    def validate_habits(self, v):
        if not isinstance(v, dict):
            raise serializers.ValidationError("An object.")
        return v

    validate_obstetric = validate_habits


class PatientHistoryView(APIView):
    """GET/PUT /clinical/patient-history/<patient_id>/"""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    queryset = PatientHistory.objects.none()  # for RoleBasedModelPermissions

    def _patient(self, request, patient_id):
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first()
        if patient is None:
            raise serializers.ValidationError({"patient": "Not found."})
        return patient

    def get(self, request, patient_id):
        patient = self._patient(request, patient_id)
        record = PatientHistory.objects.filter(patient=patient).first() or PatientHistory(patient=patient)
        return Response(PatientHistorySerializer(record).data)

    def put(self, request, patient_id):
        if not request.user.has_perm("clinical.change_patienthistory") and not request.user.has_perm("clinical.add_patienthistory"):
            return Response({"detail": "You can't edit patient history."}, status=403)
        patient = self._patient(request, patient_id)
        record, _ = PatientHistory.objects.get_or_create(patient=patient, defaults={"hospital_id": patient.hospital_id})
        ser = PatientHistorySerializer(record, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save(updated_by=request.user)
        return Response(ser.data)


def _age(dob):
    if not dob:
        return None
    today = timezone.localdate()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


class PatientHeaderView(APIView):
    """GET /clinical/patient-header/<patient_id>/ — the summary cards shown
    above every clinical screen."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    queryset = PatientHistory.objects.none()

    def get(self, request, patient_id):
        from apps.opd.models import VitalsReading
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"detail": "Not found."}, status=404)
        h = PatientHistory.objects.filter(patient=patient).first()
        latest = {}
        for r in RiskAssessment.objects.filter(patient=patient).order_by("assessed_at"):
            latest[r.tool] = r
        v = VitalsReading.objects.filter(encounter__patient=patient).order_by("-recorded_at").first()
        return Response({
            "patient": {"id": patient.pk, "name": patient.full_name, "uhid": patient.uhid, "age": _age(patient.date_of_birth),
                        "gender": patient.gender, "blood_group": patient.blood_group},
            "allergies": list(patient.allergies.filter(status="active").values("allergen", "severity", "reaction")),
            "conditions": [c for c in (h.past_medical if h else []) if c.get("ongoing", True)],
            "surgeries": h.surgical if h else [],
            "family": h.family if h else [],
            "habits": {k: val for k, val in (h.habits if h else {}).items() if val and val not in ("never", "no", "none")},
            "obstetric": h.obstetric if h else {},
            "risks": [{"tool": r.get_tool_display(), "level": r.risk_level, "score": r.score, "at": r.assessed_at}
                      for r in latest.values() if r.risk_level in scoring.HIGH_RISK_LEVELS],
            "alerts": patient.clinical_alerts.filter(acknowledged_at__isnull=True).count(),
            "last_vitals": {"at": v.recorded_at, "bp": f"{v.bp_systolic}/{v.bp_diastolic}" if v.bp_systolic else "", "pulse": v.pulse,
                            "temperature_c": v.temperature_c, "spo2": v.spo2, "weight_kg": v.weight_kg} if v else None,
            "history_updated_at": h.updated_at if h else None,
        })
