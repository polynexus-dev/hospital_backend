from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, model_serializer
from apps.core.modules import require_module

from .models import (
    Ambulance,
    AmbulanceTrip,
    AmbulanceVitals,
    EquipmentAsset,
    HousekeepingTask,
    InstrumentSet,
    MaintenanceRecord,
    SterileBatch,
    SterilizationCycle,
)

_user_name = lambda f: serializers.CharField(source=f"{f}.get_full_name", read_only=True, default=None)  # noqa: E731


class AmbulanceViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(Ambulance, read_only=("device_token",))
    queryset = Ambulance.objects.all()
    filterset_fields = ["status", "kind"]
    audited_fields = ("vehicle_number", "status")

    @action(detail=True, methods=["post"])
    def rotate_device_token(self, request, pk=None):
        import secrets

        amb = self.get_object()
        amb.device_token = secrets.token_hex(24)
        amb.save(update_fields=["device_token"])
        return Response({"device_token": amb.device_token})


TRIP_TRANSITIONS = {
    "dispatch": ("requested", "dispatched", "dispatched_at"),
    "at_scene": ("dispatched", "at_scene", "at_scene_at"),
    "depart_scene": ("at_scene", "en_route", "departed_scene_at"),
    "arrive": ("en_route", "arrived", "arrived_at"),
    "complete": ("arrived", "completed", None),
}


class AmbulanceVitalsSerializer(model_serializer(AmbulanceVitals)):
    pass


class AmbulanceTripSerializer(model_serializer(AmbulanceTrip, read_only=("status", "dispatched_at", "at_scene_at", "departed_scene_at", "arrived_at"), extra={
    "vehicle_number": serializers.CharField(source="ambulance.vehicle_number", read_only=True),
    "patient_name": serializers.CharField(source="patient.full_name", read_only=True, default=None),
})):
    latest_vitals = serializers.SerializerMethodField()

    def get_latest_vitals(self, obj):
        v = obj.vitals.first()
        return AmbulanceVitalsSerializer(v).data if v else None


class AmbulanceTripViewSet(TenantCRUDViewSet):
    serializer_class = AmbulanceTripSerializer
    queryset = AmbulanceTrip.objects.select_related("ambulance", "patient").prefetch_related("vitals")
    filterset_fields = ["status", "ambulance", "patient", "trip_type"]
    audited_fields = ("status",)

    @action(detail=True, methods=["post"], url_path=r"transition/(?P<step>dispatch|at_scene|depart_scene|arrive|complete)")
    def transition(self, request, pk=None, step=None):
        trip = self.get_object()
        src, dst, stamp = TRIP_TRANSITIONS[step]
        if trip.status != src:
            return Response({"detail": f"Trip is {trip.status}; '{step}' needs it to be {src}."}, status=400)
        trip.status = dst
        if stamp:
            setattr(trip, stamp, timezone.now())
        if step == "dispatch":
            trip.ambulance.status = Ambulance.Status.ON_TRIP
            trip.ambulance.save(update_fields=["status"])
        if step == "complete":
            trip.ambulance.status = Ambulance.Status.AVAILABLE
            trip.ambulance.save(update_fields=["status"])
        if step == "arrive" and trip.patient_id and request.data.get("create_ed_visit", True):
            from apps.emergency.models import EDVisit

            EDVisit.objects.create(
                hospital_id=trip.hospital_id, patient=trip.patient, chief_complaint=trip.chief_complaint,
                mode_of_arrival="ambulance", ambulance_trip=trip,
            )
        trip.save()
        return Response(self.get_serializer(trip).data)

    @action(detail=True, methods=["post"])
    def vitals(self, request, pk=None):
        trip = self.get_object()
        ser = AmbulanceVitalsSerializer(data={**request.data, "trip": trip.pk}, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save(hospital_id=trip.hospital_id)
        return Response(ser.data, status=201)

    @action(detail=False, methods=["get"])
    def incoming(self, request):
        """COP.4.c — ED board: trips heading to the hospital with their latest vitals."""
        qs = self.get_queryset().filter(status__in=["dispatched", "at_scene", "en_route"])
        return Response(self.get_serializer(qs, many=True).data)


class AmbulanceDeviceFeedView(APIView):
    """In-vehicle monitor / paramedic app posts vitals + GPS with the
    ambulance's device token (no user session in the vehicle)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = request.headers.get("X-Device-Token", "")
        amb = Ambulance.objects.filter(device_token=token).exclude(device_token="").first() if token else None
        if amb is None:
            return Response({"detail": "Invalid device token."}, status=401)
        require_module(amb.hospital, "support_services")
        trip = AmbulanceTrip.objects.filter(ambulance=amb, status__in=["dispatched", "at_scene", "en_route"]).order_by("-requested_at").first()
        if trip is None:
            return Response({"detail": "No active trip."}, status=409)
        if request.data.get("latitude") is not None:
            trip.last_latitude = request.data.get("latitude")
            trip.last_longitude = request.data.get("longitude")
            trip.eta_minutes = request.data.get("eta_minutes") or trip.eta_minutes
            trip.save(update_fields=["last_latitude", "last_longitude", "eta_minutes"])
        vitals = {k: request.data.get(k) for k in ("heart_rate", "bp_systolic", "bp_diastolic", "spo2", "respiratory_rate", "temperature_c", "gcs", "blood_glucose", "ecg_rhythm") if request.data.get(k) not in (None, "")}
        if vitals:
            AmbulanceVitals.objects.create(hospital_id=amb.hospital_id, trip=trip, source="device", **vitals)
        return Response({"trip": trip.pk, "accepted": True})


# --- CSSD --------------------------------------------------------------------


class InstrumentSetViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(InstrumentSet)
    queryset = InstrumentSet.objects.all()
    search_fields = ["name", "code"]


class SterilizationCycleViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SterilizationCycle, read_only=("result", "operator"))
    queryset = SterilizationCycle.objects.all()
    filterset_fields = ["result", "sterilizer", "method"]
    actor_field = "operator"
    audited_fields = ("result", "biological_indicator_passed", "chemical_indicator_passed")

    def perform_update(self, serializer):
        super().perform_update(serializer)
        cycle = serializer.instance
        if cycle.result == SterilizationCycle.Result.FAILED:
            # Recall every pack from a failed load.
            cycle.batches.exclude(status__in=[SterileBatch.Status.USED, SterileBatch.Status.RECALLED]).update(status=SterileBatch.Status.RECALLED)


class SterileBatchViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(SterileBatch, read_only=("batch_label", "status", "issued_at", "returned_at"), extra={
        "set_name": serializers.CharField(source="instrument_set.name", read_only=True),
        "cycle_result": serializers.CharField(source="cycle.result", read_only=True),
    })
    queryset = SterileBatch.objects.select_related("instrument_set", "cycle")
    filterset_fields = ["status", "instrument_set", "cycle", "used_for_patient"]
    search_fields = ["batch_label"]

    def perform_create(self, serializer):
        from rest_framework.exceptions import ValidationError

        if serializer.validated_data["cycle"].result == SterilizationCycle.Result.FAILED:
            raise ValidationError({"cycle": "Cannot create packs from a failed cycle."})
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        b = self.get_object()
        if b.cycle.result != SterilizationCycle.Result.PASSED:
            return Response({"detail": "Pack cannot be issued until the cycle's indicators have passed."}, status=400)
        if b.expires_on < timezone.localdate():
            b.status = SterileBatch.Status.EXPIRED
            b.save(update_fields=["status"])
            return Response({"detail": "Pack sterility has expired — reprocess."}, status=400)
        if b.status != SterileBatch.Status.STERILE:
            return Response({"detail": f"Pack is {b.status}."}, status=400)
        b.status = SterileBatch.Status.ISSUED
        b.issued_to = str(request.data.get("issued_to", ""))[:100]
        b.issued_at = timezone.now()
        b.save()
        return Response(self.get_serializer(b).data)

    @action(detail=True, methods=["post"])
    def use(self, request, pk=None):
        b = self.get_object()
        b.status = SterileBatch.Status.USED
        b.used_for_patient_id = request.data.get("patient") or b.used_for_patient_id
        b.used_in_surgery_id = request.data.get("ot_schedule") or b.used_in_surgery_id
        b.save()
        return Response(self.get_serializer(b).data)

    @action(detail=True, methods=["post"], url_path="return")
    def return_pack(self, request, pk=None):
        b = self.get_object()
        b.status = SterileBatch.Status.RETURNED
        b.returned_at = timezone.now()
        b.return_count_ok = bool(request.data.get("count_ok"))
        b.return_remarks = str(request.data.get("remarks", ""))[:255]
        b.save()
        return Response(self.get_serializer(b).data)


# --- Housekeeping ------------------------------------------------------------


class HousekeepingTaskViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(HousekeepingTask, read_only=("requested_by", "started_at", "completed_at", "verified_by", "verified_at"), extra={
        "assigned_to_name": _user_name("assigned_to"),
    })
    queryset = HousekeepingTask.objects.all()
    filterset_fields = ["status", "task_type", "assigned_to", "priority"]
    actor_field = "requested_by"

    def _set(self, request, status, stamp=None, **extra):
        t = self.get_object()
        t.status = status
        if stamp:
            setattr(t, stamp, timezone.now())
        for k, v in extra.items():
            setattr(t, k, v)
        t.save()
        if status == HousekeepingTask.Status.VERIFIED and t.bed_id and t.task_type == HousekeepingTask.TaskType.TERMINAL:
            from apps.facilities.models import Bed

            Bed.objects.filter(pk=t.bed_id, status="cleaning").update(status="available")
        return Response(self.get_serializer(t).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._set(request, HousekeepingTask.Status.IN_PROGRESS, "started_at")

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        return self._set(request, HousekeepingTask.Status.DONE, "completed_at", checklist=request.data.get("checklist") or {}, waste_bags=request.data.get("waste_bags") or {})

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        return self._set(request, HousekeepingTask.Status.VERIFIED, "verified_at", verified_by=request.user)


# --- Equipment ------------------------------------------------------------------


class EquipmentAssetSerializer(model_serializer(EquipmentAsset)):
    next_pm_due = serializers.DateField(read_only=True)
    next_calibration_due = serializers.DateField(read_only=True)


class EquipmentAssetViewSet(TenantCRUDViewSet):
    serializer_class = EquipmentAssetSerializer
    queryset = EquipmentAsset.objects.all()
    filterset_fields = ["status", "category", "department", "is_critical"]
    search_fields = ["asset_tag", "name", "serial_number"]
    audited_fields = ("status", "location", "amc_until")

    @action(detail=False, methods=["get"])
    def due(self, request):
        """PM / calibration / AMC / warranty falling due within ?days (default 30)."""
        horizon = timezone.localdate() + timedelta(days=int(request.query_params.get("days", 30)))
        out = []
        for a in self.get_queryset().exclude(status=EquipmentAsset.Status.CONDEMNED):
            for kind, when in (("pm", a.next_pm_due), ("calibration", a.next_calibration_due), ("amc", a.amc_until), ("warranty", a.warranty_until)):
                if when and when <= horizon:
                    out.append({"asset_id": a.pk, "asset_tag": a.asset_tag, "name": a.name, "due": kind, "due_on": when, "overdue": when < timezone.localdate(), "is_critical": a.is_critical})
        return Response(sorted(out, key=lambda r: r["due_on"]))


class MaintenanceRecordViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MaintenanceRecord, read_only=("reported_by", "closed_at", "downtime_hours"), extra={
        "asset_name": serializers.CharField(source="asset.name", read_only=True),
    })
    queryset = MaintenanceRecord.objects.select_related("asset")
    filterset_fields = ["asset", "kind", "status"]
    actor_field = "reported_by"

    def perform_create(self, serializer):
        super().perform_create(serializer)
        rec = serializer.instance
        if rec.kind == MaintenanceRecord.Kind.BREAKDOWN:
            rec.asset.status = EquipmentAsset.Status.BREAKDOWN
            rec.asset.save(update_fields=["status"])

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        rec = self.get_object()
        rec.status = MaintenanceRecord.Status.CLOSED
        rec.closed_at = timezone.now()
        rec.work_done = request.data.get("work_done", rec.work_done)
        rec.downtime_hours = round((rec.closed_at - rec.reported_at).total_seconds() / 3600, 2)
        rec.save()
        asset = rec.asset
        asset.status = EquipmentAsset.Status.IN_USE
        if rec.kind == MaintenanceRecord.Kind.PREVENTIVE:
            asset.last_pm_on = timezone.localdate()
        if rec.kind == MaintenanceRecord.Kind.CALIBRATION:
            asset.last_calibrated_on = timezone.localdate()
        asset.save()
        return Response(self.get_serializer(rec).data)
