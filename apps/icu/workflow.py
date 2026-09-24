"""NABH COP.5 additions: criteria, severity scoring, outcome, care bundles,
fluid balance."""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from . import scoring
from .models import CareBundleLog, ICUAdmission, ICUCriterion


class ICUAdmissionWorkflowMixin:
    @action(detail=False, methods=["post"])
    def score(self, request):
        """Stateless calculator — POST {scale, inputs} → score + mortality."""
        try:
            s, m = scoring.compute(request.data.get("scale"), request.data.get("inputs") or {})
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response({"score": s, "predicted_mortality": m})

    @action(detail=True, methods=["post"])
    def discharge(self, request, pk=None):
        icu = self.get_object()
        outcome = request.data.get("outcome")
        if outcome not in ICUAdmission.Outcome.values:
            return Response({"outcome": f"One of {ICUAdmission.Outcome.values}."}, status=400)
        if outcome == ICUAdmission.Outcome.WARD and not request.data.get("discharge_criteria_met"):
            return Response({"discharge_criteria_met": "Record the discharge criteria met before shifting to ward."}, status=400)
        icu.outcome = outcome
        icu.discharge_criteria_met = request.data.get("discharge_criteria_met") or []
        icu.discharged_at = timezone.now()
        icu.save()
        return Response(self.get_serializer(icu).data)

    @action(detail=True, methods=["get"])
    def fluid_balance(self, request, pk=None):
        """COP.5.d — intake/output totals for the underlying IPD admission."""
        from apps.nursing.models import IntakeOutput

        icu = self.get_object()
        ct = ContentType.objects.get_for_model(icu.admission)
        qs = IntakeOutput.objects.filter(content_type=ct, object_id=str(icu.admission_id))
        if request.query_params.get("hours"):
            qs = qs.filter(recorded_at__gte=timezone.now() - timezone.timedelta(hours=int(request.query_params["hours"])))
        agg = qs.aggregate(i=Sum("intake_ml"), o=Sum("output_ml"))
        intake, output = agg["i"] or 0, agg["o"] or 0
        return Response({"intake_ml": intake, "output_ml": output, "balance_ml": intake - output, "entries": qs.count()})


class ICUCriterionViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(ICUCriterion)
    queryset = ICUCriterion.objects.all()
    filterset_fields = ["kind", "is_active"]


class CareBundleLogViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(CareBundleLog, read_only=("compliant", "logged_by"), extra={
        "required_elements": serializers.SerializerMethodField(),
    })
    queryset = CareBundleLog.objects.all()
    filterset_fields = ["icu_admission", "bundle", "compliant"]
    actor_field = "logged_by"

    def get_serializer_class(self):
        cls = super().get_serializer_class()
        cls.get_required_elements = lambda self, obj: CareBundleLog.BUNDLES.get(obj.bundle, [])
        return cls

    @action(detail=False, methods=["get"])
    def definitions(self, request):
        return Response(CareBundleLog.BUNDLES)


DEFAULT_CRITERIA = [
    ("admission", "RESP_FAILURE", "Acute respiratory failure needing ventilatory support (invasive/NIV)"),
    ("admission", "SHOCK", "Haemodynamic instability needing vasopressors / inotropes"),
    ("admission", "GCS_LE_8", "Altered sensorium with GCS ≤ 8 / unprotected airway"),
    ("admission", "POST_OP_HIGH_RISK", "High-risk post-operative monitoring"),
    ("admission", "MULTI_ORGAN", "Multi-organ dysfunction (SOFA ≥ 2 from baseline)"),
    ("admission", "POST_ARREST", "Post cardiac arrest care"),
    ("discharge", "STABLE_HAEMO", "Haemodynamically stable without vasopressors for 24 h"),
    ("discharge", "OFF_VENT", "Off ventilator, maintaining airway, SpO2 ≥ 94% on ≤ 4 L O2"),
    ("discharge", "NO_ICU_INTERVENTION", "No longer needs ICU-level monitoring or interventions"),
]


def seed_criteria(hospital, get_model=None):
    model = get_model("icu", "ICUCriterion") if get_model else ICUCriterion
    if not model.objects.filter(hospital=hospital).exists():
        model.objects.bulk_create([model(hospital=hospital, kind=k, code=c, description=d) for k, c, d in DEFAULT_CRITERIA])
