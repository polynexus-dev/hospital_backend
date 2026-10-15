"""OT tariff templates: surgery grade master and the grade x room-category
tariff (surgeon / anaesthetist / OT / assistant). Kept under finance so the
rates are the accounts team's to change; OT staff can read them."""
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, model_serializer

from .models_ot import ROOM_CATEGORIES, OTTariff, SurgeryGrade


class SurgeryGradeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SurgeryGrade)
    queryset = SurgeryGrade.objects.all()
    filterset_fields = ["is_active"]
    search_fields = ["code", "name"]
    audited_fields = ("code", "name", "is_active")


class OTTariffSerializer(model_serializer(OTTariff, extra={
    "grade_name": serializers.CharField(source="grade.name", read_only=True),
    "total": serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True),
})):
    def validate_room_category(self, value):
        if value and value not in ROOM_CATEGORIES:
            raise serializers.ValidationError(f"One of {', '.join(ROOM_CATEGORIES)}, or blank for any room.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        for f in ("surgeon_fee", "anaesthetist_fee", "ot_charge", "assistant_fee"):
            if attrs.get(f) is not None and attrs[f] < 0:
                raise serializers.ValidationError({f: "Can't be negative."})
        grade = attrs.get("grade", getattr(self.instance, "grade", None))
        room = attrs.get("room_category", getattr(self.instance, "room_category", ""))
        hospital_id = getattr(self.context["request"].user, "hospital_id", None)
        clash = OTTariff.objects.filter(hospital_id=hospital_id, grade=grade, room_category=room)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"room_category": "This grade already has a tariff for that room category."})
        return attrs


class OTTariffViewSet(TenantCRUDViewSet):
    serializer_class = OTTariffSerializer
    queryset = OTTariff.objects.select_related("grade")
    filterset_fields = ["grade", "room_category", "is_active"]
    audited_fields = ("surgeon_fee", "anaesthetist_fee", "ot_charge", "assistant_fee", "is_active")

    @action(detail=False, methods=["get"])
    def quote(self, request):
        """GET ?grade=&room_category= — the tariff that would apply (room row, else the grade's any-room row)."""
        from apps.ot.charges import tariff_for

        grade = SurgeryGrade.objects.filter(hospital_id=request.user.hospital_id, pk=request.query_params.get("grade") or 0).first()
        if grade is None:
            return Response({"grade": "Pick a surgery grade."}, status=status.HTTP_400_BAD_REQUEST)
        room = request.query_params.get("room_category", "")
        tariff = tariff_for(request.user.hospital_id, grade, room)
        if tariff is None:
            return Response({"detail": f"No OT tariff for {grade.name} in that room category."}, status=status.HTTP_404_NOT_FOUND)
        data = self.get_serializer(tariff).data
        data["matched"] = "room" if tariff.room_category == room else "any_room"
        return Response(data)
