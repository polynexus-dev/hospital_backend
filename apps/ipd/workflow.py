"""NABH AAC.5 / AAC.6 additions to IPD: admission rules & notifications,
discharge planning with clearances, bed board and bed-availability
prediction."""
from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.dispatch import receiver
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer
from apps.core.permissions import RequiresClinicalDetailPermission

from .models import Admission, AdmissionRule, DischargeClearance


def notify_departments(admission, event):
    """AAC.5.g — alert the configured departments on admit/transfer."""
    from apps.clinical.models import ClinicalAlert

    rule = AdmissionRule.objects.filter(hospital_id=admission.hospital_id, admission_type=admission.admission_type, is_active=True).first()
    departments = rule.notify_departments if rule and rule.notify_departments else ["nursing", "dietary", "billing", "pharmacy"]
    bed = admission.bed
    for dept in departments:
        ClinicalAlert.objects.create(
            hospital_id=admission.hospital_id, patient=admission.patient, alert_type=ClinicalAlert.AlertType.CDSS,
            severity=ClinicalAlert.Severity.INFO, target_department=dept,
            title=f"{event}: {admission.patient.full_name} ({admission.patient.uhid})",
            message=f"{event} to bed {bed.bed_number} ({bed.room.ward.name}). Diagnosis: {admission.admission_diagnosis or '—'}",
            object_id=str(admission.pk),
        )


class AdmissionWorkflowMixin:
    @action(detail=True, methods=["post"])
    def initiate_discharge(self, request, pk=None):
        adm = self.get_object()
        if adm.status != Admission.Status.ADMITTED:
            return Response({"detail": "Only a current admission can be discharged."}, status=400)
        adm.discharge_initiated_at = adm.discharge_initiated_at or timezone.now()
        adm.save(update_fields=["discharge_initiated_at"])
        depts = request.data.get("departments") or [d for d in DischargeClearance.Department.values]
        for d in depts:
            DischargeClearance.objects.get_or_create(hospital_id=adm.hospital_id, admission=adm, department=d)
        from apps.clinical.models import ClinicalAlert

        for d in depts:
            ClinicalAlert.objects.create(
                hospital_id=adm.hospital_id, patient=adm.patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.INFO,
                target_department=d, title=f"Discharge clearance needed: {adm.patient.full_name}", message="Discharge initiated — please clear.", object_id=str(adm.pk),
            )
        return Response(DischargeClearanceSerializer(adm.clearances.all(), many=True).data)

    @action(detail=False, methods=["get"])
    def due_for_discharge(self, request):
        """AAC.6.b — admitted patients expected to go today/tomorrow, plus
        anyone whose discharge has already been initiated."""
        horizon = timezone.localdate() + timedelta(days=int(request.query_params.get("days", 1)))
        qs = self.get_queryset().filter(status=Admission.Status.ADMITTED).filter(Q(expected_discharge_date__lte=horizon) | Q(discharge_initiated_at__isnull=False))
        rows = []
        for a in qs.select_related("patient", "bed__room__ward").prefetch_related("clearances"):
            rows.append({
                "id": a.pk, "patient": a.patient.full_name, "uhid": a.patient.uhid, "bed": a.bed.bed_number, "ward": a.bed.room.ward.name,
                "expected_discharge_date": a.expected_discharge_date, "discharge_initiated_at": a.discharge_initiated_at,
                "pending_clearances": [c.department for c in a.clearances.all() if c.status == "pending"],
            })
        return Response(rows)


class AdmissionRuleViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(AdmissionRule)
    queryset = AdmissionRule.objects.all()
    filterset_fields = ["admission_type", "is_active"]


class DischargeClearanceSerializer(model_serializer(DischargeClearance, read_only=("cleared_by", "cleared_at"))):
    pass


class DischargeClearanceViewSet(ClinicalCRUDViewSet):
    serializer_class = DischargeClearanceSerializer
    queryset = DischargeClearance.objects.all()
    filterset_fields = ["admission", "department", "status"]

    @action(detail=True, methods=["post"])
    def clear(self, request, pk=None):
        c = self.get_object()
        c.status = DischargeClearance.Status.NOT_APPLICABLE if request.data.get("not_applicable") else DischargeClearance.Status.CLEARED
        c.cleared_by = request.user
        c.cleared_at = timezone.now()
        c.remarks = str(request.data.get("remarks", ""))[:255]
        c.save()
        return Response(self.get_serializer(c).data)


class BedBoardView(APIView):
    """AAC.5.h occupied-bed display + AAC.5.i availability prediction."""

    permission_classes = [IsAuthenticated, RequiresClinicalDetailPermission]

    def get(self, request):
        from apps.facilities.models import Bed

        h = request.user.hospital_id
        beds = Bed.objects.filter(hospital_id=h).select_related("room__ward", "current_admission__patient")
        wards = {}
        for b in beds:
            w = wards.setdefault(b.room.ward.name, {"ward": b.room.ward.name, "total": 0, "occupied": 0, "available": 0, "other": 0, "beds": []})
            w["total"] += 1
            key = "occupied" if b.status == "occupied" else "available" if b.status == "available" else "other"
            w[key] += 1
            adm = b.current_admission
            w["beds"].append({
                "id": b.pk, "bed_number": b.bed_number, "room": b.room.room_number, "bed_type": b.bed_type, "status": b.status,
                "patient": adm.patient.full_name if adm else None, "uhid": adm.patient.uhid if adm else None,
                "admitted_at": adm.admitted_at if adm else None, "expected_discharge_date": adm.expected_discharge_date if adm else None,
            })
        total = sum(w["total"] for w in wards.values())
        occupied = sum(w["occupied"] for w in wards.values())
        return Response({
            "summary": {"total": total, "occupied": occupied, "available": sum(w["available"] for w in wards.values()), "occupancy_pct": round(occupied * 100 / total, 1) if total else 0},
            "wards": list(wards.values()),
            "prediction": predict_bed_availability(h),
        })


def predict_bed_availability(hospital_id, horizons=(24, 48, 72)):
    """Beds expected to free up = admissions with an expected discharge
    date inside the horizon, plus — for those without one — admissions
    whose length of stay will exceed the hospital's trailing-90-day
    average LOS (ALOS) within the horizon. Minus expected planned
    admissions is out of scope (no bookings table for that yet), so this
    is 'beds likely to become free', not net availability."""
    from apps.facilities.models import Bed

    now = timezone.now()
    past = Admission.objects.filter(hospital_id=hospital_id, discharged_at__gte=now - timedelta(days=90), discharged_at__isnull=False)
    alos = past.annotate(los=ExpressionWrapper(F("discharged_at") - F("admitted_at"), output_field=DurationField())).aggregate(a=Avg("los"))["a"]
    alos_h = alos.total_seconds() / 3600 if alos else 96
    current = Admission.objects.filter(hospital_id=hospital_id, status=Admission.Status.ADMITTED)
    available_now = Bed.objects.filter(hospital_id=hospital_id, status="available").count()
    out = {"available_now": available_now, "alos_hours": round(alos_h, 1), "expected_free": {}}
    for h in horizons:
        cutoff = now + timedelta(hours=h)
        n = 0
        for a in current.only("admitted_at", "expected_discharge_date"):
            if a.expected_discharge_date:
                n += a.expected_discharge_date <= cutoff.date()
            else:
                n += (a.admitted_at + timedelta(hours=alos_h)) <= cutoff
        out["expected_free"][f"{h}h"] = n
        out[f"projected_available_{h}h"] = available_now + n
    return out


def admission_created(admission):
    notify_departments(admission, "New admission")


def _on_discharge(sender, admission, **kwargs):
    """Terminal cleaning task for the vacated bed, and the MRD case file
    the ward sends down for coding and storage."""
    from apps.mrd.models import MedicalRecordFile
    from apps.support_services.models import HousekeepingTask

    ed = admission.source_ed_visit
    MedicalRecordFile.objects.get_or_create(
        hospital_id=admission.hospital_id, admission=admission,
        defaults={"patient": admission.patient, "is_mlc": bool(ed and ed.is_mlc)},
    )

    HousekeepingTask.objects.create(
        hospital_id=admission.hospital_id, task_type=HousekeepingTask.TaskType.TERMINAL, bed_id=admission.bed_id,
        location=f"Bed {admission.bed.bed_number}", priority="high", due_by=timezone.now() + timedelta(hours=1),
    )


def connect_signals():
    from .signals import patient_discharged

    patient_discharged.connect(_on_discharge, dispatch_uid="ipd_discharge_housekeeping", weak=False)
