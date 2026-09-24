"""NABH AAC.4 / COP.1.g/k additions to radiology."""
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer
from apps.core.models import Amendment

from . import dicom
from .models import RadiologyAppointment, RadiologyEquipment, RadiologyImage, RadiologyOrder, RadiologyReport, RadiologyTemplate

STATUS_STEPS = {"arrive": "arrived", "start": "in_progress", "complete": "completed", "cancel": "cancelled"}


def viewer_url(order):
    template = getattr(settings, "PACS_VIEWER_URL", "")
    if template and order.study_instance_uid:
        return template.format(study_uid=order.study_instance_uid, accession=order.accession_number)
    return None


class RadiologyOrderWorkflowMixin:
    def create(self, request, *args, **kwargs):
        """COP.1.k duplicate guard + AAC.4.l contraindication check before
        the order is accepted; both can be consciously overridden."""
        from apps.clinical.models import ClinicalAlert
        from apps.clinical.services import check_radiology_contraindications, duplicate_radiology_orders, persist_alerts
        from apps.patients.models import Patient

        from .models import RadiologyProcedure

        patient = Patient.objects.filter(pk=request.data.get("patient"), hospital_id=request.user.hospital_id).first()
        procedure = RadiologyProcedure.objects.filter(pk=request.data.get("procedure"), hospital_id=request.user.hospital_id).first()
        if patient and procedure:
            if not request.data.get("confirm_duplicate"):
                dupes = duplicate_radiology_orders(patient, procedure.pk)
                if dupes:
                    return Response({"detail": "Duplicate order — the same study was ordered in the last 24 hours.", "duplicates": dupes, "code": "duplicate_order"}, status=409)
            subject = f"{procedure.name} {'contrast' if procedure.uses_contrast else ''}"
            alerts = check_radiology_contraindications(patient, procedure.modality, subject)
            if alerts and not request.data.get("contraindication_override_reason"):
                return Response({"detail": "Contraindication for this study.", "alerts": alerts, "code": "contraindicated"}, status=409)
            if alerts:
                persist_alerts(patient, alerts, target_user=request.user)
        response = super().create(request, *args, **kwargs)
        if response.status_code == 201:
            ClinicalAlert.objects.create(
                hospital_id=request.user.hospital_id, patient=patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.INFO,
                target_department="radiology", title=f"New {procedure.get_modality_display() if procedure else ''} request: {response.data.get('accession_number', '')}",
                message=f"{procedure.name if procedure else 'Study'} for {patient.full_name if patient else ''} ({response.data.get('priority', 'routine')}).",
                object_id=str(response.data.get("id")),
            )
        return response

    @action(detail=True, methods=["post"], url_path=r"status/(?P<step>arrive|start|complete|cancel)")
    def set_status(self, request, pk=None, step=None):
        order = self.get_object()
        if order.status in (RadiologyOrder.Status.REPORTED, RadiologyOrder.Status.CANCELLED):
            return Response({"detail": f"Order is already {order.status}."}, status=400)
        order.status = STATUS_STEPS[step]
        order.save()
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def book_slot(self, request, pk=None):
        """AAC.4.g — books the study on a machine; refuses overlaps."""
        from django.utils.dateparse import parse_datetime

        order = self.get_object()
        equipment = RadiologyEquipment.objects.filter(pk=request.data.get("equipment"), hospital_id=order.hospital_id, is_active=True).first()
        start = parse_datetime(str(request.data.get("start", "")))
        if equipment is None or start is None:
            return Response({"detail": "equipment and start are required."}, status=400)
        end = start + timedelta(minutes=max(order.procedure.duration_minutes, equipment.slot_minutes))
        clash = RadiologyAppointment.objects.filter(equipment=equipment, start__lt=end, end__gt=start).exclude(order=order).exists()
        if clash:
            return Response({"detail": "Slot already booked on this machine."}, status=409)
        with transaction.atomic():
            RadiologyAppointment.objects.update_or_create(order=order, defaults={"hospital_id": order.hospital_id, "equipment": equipment, "start": start, "end": end, "technician_id": request.data.get("technician")})
            order.status = RadiologyOrder.Status.SCHEDULED
            order.save()
        return Response(self.get_serializer(order).data)

    @action(detail=True, methods=["post"])
    def upload_images(self, request, pk=None):
        order = self.get_object()
        saved = []
        for f in request.FILES.getlist("files"):
            head = f.read(64 * 1024)
            f.seek(0)
            tags = dicom.read_tags(head)
            img = RadiologyImage.objects.create(
                hospital_id=order.hospital_id, order=order, file=f, is_dicom=dicom.is_dicom(head), uploaded_by=request.user,
                study_instance_uid=tags.get("study_instance_uid", ""), series_instance_uid=tags.get("series_instance_uid", ""),
                sop_instance_uid=tags.get("sop_instance_uid", ""), modality=tags.get("modality", ""),
            )
            if img.study_instance_uid and not order.study_instance_uid:
                order.study_instance_uid = img.study_instance_uid
                order.save()
            saved.append(img.pk)
        return Response({"images": saved, "study_instance_uid": order.study_instance_uid, "viewer_url": viewer_url(order)})

    @action(detail=True, methods=["get"])
    def viewer(self, request, pk=None):
        order = self.get_object()
        return Response({
            "viewer_url": viewer_url(order), "study_instance_uid": order.study_instance_uid,
            "images": [{"id": i.pk, "url": i.file.url, "is_dicom": i.is_dicom} for i in order.images.all()],
        })


class RadiologyReportWorkflowMixin:
    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """AAC.4.i — amendments to a signed report are flagged, not hidden."""
        report = self.get_object()
        if not report.finalized_at:
            return Response({"detail": "Report isn't signed yet — edit it directly."}, status=400)
        reason = str(request.data.get("reason", "")).strip()
        changes = {f: str(request.data[f]) for f in ("findings", "impression") if f in request.data}
        if not reason or not changes:
            return Response({"detail": "reason and findings/impression are required."}, status=400)
        ct = ContentType.objects.get_for_model(RadiologyReport)
        with transaction.atomic():
            for field, new in changes.items():
                Amendment.objects.create(
                    hospital_id=report.hospital_id, content_type=ct, object_id=str(report.pk), field_name=field,
                    previous_value=getattr(report, field), corrected_value=new, reason=reason, amended_by=request.user,
                )
            RadiologyReport.objects.filter(pk=report.pk).update(is_amended=True, **changes)
        report.refresh_from_db()
        order = report.radiology_order
        if order.ordered_by_id:
            from apps.clinical.models import ClinicalAlert

            ClinicalAlert.objects.create(
                hospital_id=report.hospital_id, patient=order.patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.WARNING,
                target_user=order.ordered_by, title=f"AMENDED radiology report {order.accession_number}", message=reason, object_id=str(report.pk),
            )
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=["get"])
    def amendments(self, request, pk=None):
        report = self.get_object()
        rows = Amendment.objects.filter(content_type=ContentType.objects.get_for_model(RadiologyReport), object_id=str(report.pk)).values(
            "field_name", "previous_value", "corrected_value", "reason", "amended_by__email", "amended_at",
        )
        return Response(list(rows))


def notify_radiology_new_order(order):
    """AAC.4.c — the radiology department's alert inbox gets every new booking as it is placed."""
    from apps.clinical.models import ClinicalAlert

    ClinicalAlert.objects.create(
        hospital=order.hospital, patient=order.patient, alert_type=ClinicalAlert.AlertType.NEW_ORDER,
        severity=ClinicalAlert.Severity.WARNING if order.priority in ("urgent", "stat") else ClinicalAlert.Severity.INFO,
        target_department="radiology",
        title=f"New {order.priority} radiology order: {order.procedure.name}",
        message=f"{order.patient.full_name} ({order.patient.uhid}) — ordered by {order.ordered_by.get_full_name() if order.ordered_by else 'unknown'}.",
        content_type=ContentType.objects.get_for_model(order), object_id=str(order.pk),
    )


def notify_radiology_report_ready(report):
    from apps.clinical.notify import notify_patient

    order = report.radiology_order
    notify_patient(order.patient, "report_ready", {"report_type": "Radiology", "order_number": order.accession_number})
    RadiologyOrder.objects.filter(pk=order.pk).update(patient_notified_at=timezone.now())


class RadiologyTemplateViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(RadiologyTemplate)
    queryset = RadiologyTemplate.objects.all()
    filterset_fields = ["modality", "procedure", "is_active"]


class RadiologyEquipmentViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(RadiologyEquipment)
    queryset = RadiologyEquipment.objects.all()
    filterset_fields = ["modality", "is_active"]

    @action(detail=True, methods=["get"])
    def slots(self, request, pk=None):
        """Free slots for a date (default today) on this machine."""
        eq = self.get_object()
        from datetime import date as date_cls

        try:
            day = date_cls.fromisoformat(request.query_params.get("date")) if request.query_params.get("date") else timezone.localdate()
        except ValueError:
            return Response({"date": "YYYY-MM-DD"}, status=400)
        tz = timezone.get_current_timezone()
        cur = timezone.make_aware(datetime.combine(day, eq.start_time), tz)
        end = timezone.make_aware(datetime.combine(day, eq.end_time), tz)
        booked = list(RadiologyAppointment.objects.filter(equipment=eq, start__lt=end, end__gt=cur).values_list("start", "end"))
        free = []
        step = timedelta(minutes=eq.slot_minutes)
        while cur + step <= end:
            if not any(s < cur + step and e > cur for s, e in booked) and cur > timezone.now():
                free.append(cur.isoformat())
            cur += step
        return Response({"equipment": eq.name, "date": day, "free_slots": free})


class RadiologyAppointmentViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(RadiologyAppointment, extra={
        "accession_number": serializers.CharField(source="order.accession_number", read_only=True),
        "patient_name": serializers.CharField(source="order.patient.full_name", read_only=True),
        "procedure_name": serializers.CharField(source="order.procedure.name", read_only=True),
    })
    queryset = RadiologyAppointment.objects.select_related("order__patient", "order__procedure", "equipment")
    filterset_fields = ["equipment"]
    http_method_names = ["get", "head", "options", "delete"]
