import csv

from django.conf import settings
from django.db.models import Count, Max
from django.http import HttpResponse
from django_celery_beat.models import PeriodicTask
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.appointments.models import Appointment
from apps.enquiries.models import Enquiry
from apps.patients.models import Patient

from .models import HISBillingRecord, HISVisit
from .serializers import HISBillingRecordSerializer, HISVisitSerializer

EXPORTERS = {
    "patients": (
        Patient,
        ["id", "first_name", "last_name", "mobile", "email", "date_of_birth", "gender", "city", "preferred_language", "created_at"],
    ),
    "enquiries": (
        Enquiry,
        ["id", "name", "mobile", "email", "source", "stage", "department_id", "urgency", "created_at"],
    ),
    "appointments": (
        Appointment,
        ["id", "patient_id", "doctor_id", "status", "source", "created_at"],
    ),
}


class DataExportView(APIView):
    """Self-service full export in open CSV, no exit fee, no vendor
    lock-in — the DPDP-era selling point in §13 / Part C. FHIR R4 export is
    a P2/P4 concern (structured resource modeling); this covers the
    "open CSV" half of the requirement now."""

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)  # raw CSV download, not a JSON API response — nothing for the schema to describe
    def get(self, request, model_name):
        exporter = EXPORTERS.get(model_name)
        if exporter is None:
            return HttpResponse(f"Unknown export model '{model_name}'. Choose from: {', '.join(EXPORTERS)}.", status=400)

        model, fields = exporter
        queryset = model.objects.filter(hospital_id=request.user.hospital_id).values(*fields)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{model_name}_export.csv"'
        writer = csv.DictWriter(response, fieldnames=fields)
        writer.writeheader()
        for row in queryset.iterator():
            writer.writerow(row)
        return response


class FHIRExportView(APIView):
    """HL7 FHIR R4 JSON Export API for Patients and Appointments."""

    permission_classes = [IsAuthenticated]

    @extend_schema(exclude=True)
    def get(self, request, resource_type="patients"):
        from django.http import JsonResponse
        from .fhir import appointment_to_fhir_resource, patient_to_fhir_resource, to_fhir_bundle

        hospital_id = request.user.hospital_id
        fhir_resources = []

        if resource_type in ("patients", "all"):
            patients = Patient.objects.filter(hospital_id=hospital_id)
            for p in patients:
                fhir_resources.append(patient_to_fhir_resource(p))

        if resource_type in ("appointments", "all"):
            appointments = Appointment.objects.filter(hospital_id=hospital_id).select_related("patient", "doctor", "slot")
            for appt in appointments:
                fhir_resources.append(appointment_to_fhir_resource(appt))

        bundle = to_fhir_bundle(fhir_resources)
        response = JsonResponse(bundle)
        response["Content-Disposition"] = f'attachment; filename="fhir_{resource_type}_bundle.json"'
        return response



class IntegrationHealthView(APIView):
    """Admin Console panel — is the HIS integration actually wired up (or
    still the no-op stub), is the Celery beat schedule running, how fresh
    is the HIS cache. Admin Console only, gated with IsAdminUser (is_staff),
    matching how apps.core.views.AuditLogViewSet already elevates staff
    users elsewhere."""

    permission_classes = [IsAdminUser]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        hospital = request.user.hospital

        his_visits = HISVisit.objects.filter(hospital_id=request.user.hospital_id).aggregate(
            count=Count("id"), latest=Max("updated_at")
        )
        his_billing = HISBillingRecord.objects.filter(hospital_id=request.user.hospital_id).aggregate(
            count=Count("id"), latest=Max("updated_at")
        )

        celery_tasks = []
        for name, schedule in settings.CELERY_BEAT_SCHEDULE.items():
            task_path = schedule["task"]
            try:
                periodic_task = PeriodicTask.objects.filter(task=task_path).first()
            except Exception:
                # e.g. django_celery_beat tables not migrated yet on a fresh install.
                periodic_task = None

            if periodic_task is None:
                celery_tasks.append(
                    {"name": name, "task": task_path, "last_run_at": None, "enabled": None, "total_run_count": 0}
                )
            else:
                celery_tasks.append(
                    {
                        "name": name,
                        "task": task_path,
                        "last_run_at": periodic_task.last_run_at,
                        "enabled": periodic_task.enabled,
                        "total_run_count": periodic_task.total_run_count,
                    }
                )

        return Response(
            {
                "his_connector": settings.HIS_CONNECTOR,
                "his_visits": {"count": his_visits["count"], "latest_synced_at": his_visits["latest"]},
                "his_billing": {"count": his_billing["count"], "latest_synced_at": his_billing["latest"]},
                "celery_tasks": celery_tasks,
                "data_retention_days": settings.DEFAULT_DATA_RETENTION_DAYS,
                "is_on_premise": hospital.is_on_premise,
            }
        )


class HISVisitViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HISVisitSerializer
    queryset = HISVisit.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["patient", "visit_type"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return HISVisit.objects.none()
        return HISVisit.objects.filter(hospital_id=self.request.user.hospital_id)


class HISBillingRecordViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = HISBillingRecordSerializer
    queryset = HISBillingRecord.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["patient", "status"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return HISBillingRecord.objects.none()
        return HISBillingRecord.objects.filter(hospital_id=self.request.user.hospital_id)
