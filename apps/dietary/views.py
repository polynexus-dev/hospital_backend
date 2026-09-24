from datetime import date

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from .models import MEALS, DietConsultation, DietOrder, DietType, MealService, MenuItem

_patient = {
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    "patient_uhid": serializers.CharField(source="patient.uhid", read_only=True),
}


class DietTypeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(DietType)
    queryset = DietType.objects.all()
    filterset_fields = ["is_therapeutic", "is_active"]


class DietOrderViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(DietOrder, read_only=("ordered_by",), extra={
        **_patient, "diet_name": serializers.CharField(source="diet_type.name", read_only=True),
        "bed": serializers.CharField(source="admission.bed.bed_number", read_only=True),
        "ward": serializers.CharField(source="admission.bed.room.ward.name", read_only=True),
    })
    queryset = DietOrder.objects.select_related("patient", "diet_type", "admission__bed__room__ward")
    filterset_fields = ["patient", "admission", "status", "diet_type"]
    actor_field = "ordered_by"
    audited_fields = ("diet_type", "texture", "status", "calories")

    def perform_create(self, serializer):
        """One active order per admission — a new order supersedes the old."""
        with transaction.atomic():
            DietOrder.objects.filter(admission=serializer.validated_data["admission"], status__in=["active", "npo"]).update(status=DietOrder.Status.STOPPED, end_date=timezone.localdate())
            super().perform_create(serializer)
            from apps.clinical.models import Allergy

            order = serializer.instance
            food = list(Allergy.objects.filter(patient=order.patient, allergen_type="food", status="active").values_list("allergen", flat=True))
            if food and not order.food_allergies:
                order.food_allergies = ", ".join(food)[:255]
                order.save(update_fields=["food_allergies"])


class DietConsultationViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(DietConsultation, read_only=("bmi", "dietitian"), extra=_patient)
    queryset = DietConsultation.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "nutritional_risk"]
    actor_field = "dietitian"


class MenuItemViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MenuItem, extra={"diet_name": serializers.CharField(source="diet_type.name", read_only=True)})
    queryset = MenuItem.objects.select_related("diet_type")
    filterset_fields = ["diet_type", "meal", "weekday"]


def _menu_for(order, meal, day):
    q = MenuItem.objects.filter(diet_type=order.diet_type, meal=meal)
    item = q.filter(weekday=day.weekday()).first() or q.filter(weekday__isnull=True).first()
    return item.items if item else ""


class MealServiceViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MealService, read_only=("delivered_at", "delivered_by"), extra={
        "patient_name": serializers.CharField(source="diet_order.patient.full_name", read_only=True),
        "bed": serializers.CharField(source="diet_order.admission.bed.bed_number", read_only=True),
        "ward": serializers.CharField(source="diet_order.admission.bed.room.ward.name", read_only=True),
        "diet_name": serializers.CharField(source="diet_order.diet_type.name", read_only=True),
        "texture": serializers.CharField(source="diet_order.texture", read_only=True),
        "food_allergies": serializers.CharField(source="diet_order.food_allergies", read_only=True),
    })
    queryset = MealService.objects.select_related("diet_order__patient", "diet_order__diet_type", "diet_order__admission__bed__room__ward")
    filterset_fields = ["service_date", "meal", "status", "diet_order"]

    @action(detail=False, methods=["post"])
    def generate(self, request):
        """Kitchen tray list for a meal: one tray per active diet order of an
        admitted patient; NPO orders produce a HELD tray so the kitchen
        sees why nothing goes to that bed."""
        meal = request.data.get("meal")
        if meal not in dict(MEALS):
            return Response({"meal": f"One of {list(dict(MEALS))}"}, status=400)
        day = date.fromisoformat(request.data["date"]) if request.data.get("date") else timezone.localdate()
        created = 0
        for order in DietOrder.objects.filter(hospital_id=request.user.hospital_id, status__in=["active", "npo"], admission__status="admitted"):
            _, was_new = MealService.objects.get_or_create(
                hospital_id=order.hospital_id, diet_order=order, service_date=day, meal=meal,
                defaults={"items": _menu_for(order, meal, day), "status": MealService.Status.HELD if order.status == "npo" else MealService.Status.PLANNED},
            )
            created += was_new
        return Response({"created": created, "date": day, "meal": meal})

    @action(detail=True, methods=["post"])
    def deliver(self, request, pk=None):
        m = self.get_object()
        if m.diet_order.status == DietOrder.Status.NPO:
            return Response({"detail": "Patient is NPO — do not serve."}, status=400)
        m.status = MealService.Status.REFUSED if request.data.get("refused") else MealService.Status.DELIVERED
        m.delivered_at = timezone.now()
        m.delivered_by = request.user
        m.consumption_pct = request.data.get("consumption_pct")
        m.remarks = str(request.data.get("remarks", ""))[:255]
        m.save()
        return Response(self.get_serializer(m).data)

    @action(detail=False, methods=["get"])
    def kitchen_summary(self, request):
        """Counts by diet × texture for the kitchen to cook against."""
        from django.db.models import Count

        day = request.query_params.get("date") or str(timezone.localdate())
        meal = request.query_params.get("meal") or "lunch"
        rows = self.get_queryset().filter(service_date=day, meal=meal).exclude(status="held").values("diet_order__diet_type__name", "diet_order__texture").annotate(n=Count("id"))
        return Response(list(rows))


DEFAULT_DIETS = [
    ("Normal", "NORMAL", False, 2000), ("Diabetic", "DM", True, 1600), ("Renal", "RENAL", True, 1800), ("Cardiac / low salt", "CARDIAC", True, 1800),
    ("Soft", "SOFT", True, None), ("Clear liquid", "CLEAR", True, None), ("Full liquid", "LIQUID", True, None), ("High protein", "HP", True, 2200),
    ("Low residue", "LOWRES", True, None), ("Nil by mouth", "NPO", True, 0),
]


def seed_diets(hospital, get_model=None):
    model = get_model("dietary", "DietType") if get_model else DietType
    if not model.objects.filter(hospital=hospital).exists():
        model.objects.bulk_create([model(hospital=hospital, name=n, code=c, is_therapeutic=t, default_calories=cal) for n, c, t, cal in DEFAULT_DIETS])
