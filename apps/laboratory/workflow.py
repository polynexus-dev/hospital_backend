"""
NABH AAC.3 / COP.1.f/k/m additions to the lab: auto specimen numbers,
specimen tracking & rejection, result auto-flagging against reference and
critical limits, critical-value alerts, duplicate-order guard, repeat
flags, amendments to signed results, addenda, patient notification when a
report is ready, outsourced tests, printable labels/reports, and HL7 v2
analyser result import.
"""
import io
import re
from decimal import Decimal, InvalidOperation

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer
from apps.core.models import Amendment

from .models import LabAnalyzer, LabOrder, LabReportAddendum, LabReportTemplate, LabResult, LabTest, OutsourcedLabTest, SampleCollection

_RANGE = re.compile(r"(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)")


def _num(v):
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def compute_flag(test, value, reference_range=""):
    """AAC.3.i — numeric result vs. the test's limits. Returns None when
    the value isn't numeric or no limits are configured (the entered flag
    then stands)."""
    v = _num(value)
    if v is None:
        return None
    lo, hi = test.ref_low, test.ref_high
    if lo is None and hi is None:
        m = _RANGE.search(reference_range or test.reference_range or "")
        if m:
            lo, hi = Decimal(m.group(1)), Decimal(m.group(2))
    if test.critical_low is not None and v <= test.critical_low:
        return LabResult.Flag.CRITICAL
    if test.critical_high is not None and v >= test.critical_high:
        return LabResult.Flag.CRITICAL
    if lo is not None and v < lo:
        return LabResult.Flag.LOW
    if hi is not None and v > hi:
        return LabResult.Flag.HIGH
    if lo is not None or hi is not None:
        return LabResult.Flag.NORMAL
    return None


def apply_flag_and_alert(result):
    flag = compute_flag(result.lab_test, result.value, result.reference_range)
    # Auto-flagging may only escalate: a technician's manual CRITICAL
    # stands even when the test has no critical limits configured.
    if result.flag == LabResult.Flag.CRITICAL:
        flag = None
    if flag and flag != result.flag:
        LabResult.objects.filter(pk=result.pk).update(flag=flag)
        result.flag = flag
    if result.flag == LabResult.Flag.CRITICAL:
        from apps.clinical.services import raise_critical_result_alert

        order = result.lab_order
        raise_critical_result_alert(
            patient=order.patient, test_name=result.lab_test.name, value=result.value, unit=result.unit or result.lab_test.unit,
            flag="critical", source=result, ordering_user=order.ordered_by,
        )


def next_specimen_number(hospital_id):
    day = timezone.localdate()
    prefix = f"S{day:%y%m%d}"
    n = SampleCollection.objects.filter(hospital_id=hospital_id, barcode__startswith=prefix).count() + 1
    return f"{prefix}{n:05d}"


def notify_report_ready(order):
    """AAC.3.k — patient (WhatsApp → SMS fallback) + ordering clinician."""
    from apps.clinical.models import ClinicalAlert
    from apps.clinical.notify import notify_patient

    notify_patient(order.patient, "report_ready", {"report_type": "Laboratory", "order_number": order.order_number})
    if order.ordered_by_id:
        ClinicalAlert.objects.create(
            hospital_id=order.hospital_id, patient=order.patient, alert_type=ClinicalAlert.AlertType.CDSS, severity=ClinicalAlert.Severity.INFO,
            target_user=order.ordered_by, title=f"Lab report ready: {order.order_number}", message=f"Results for {order.patient.full_name} are verified.",
            object_id=str(order.pk),
        )
    LabOrder.objects.filter(pk=order.pk).update(patient_notified_at=timezone.now())


class LabOrderWorkflowMixin:
    def create(self, request, *args, **kwargs):
        """COP.1.k — refuse a same-test order placed within 24h unless the
        clinician explicitly confirms it's intended."""
        from apps.clinical.services import duplicate_lab_orders

        patient_id = request.data.get("patient")
        tests = [int(t) for t in (request.data.get("ordered_tests") or []) if str(t).isdigit()]
        if patient_id and tests and not request.data.get("confirm_duplicate"):
            from apps.patients.models import Patient

            patient = Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first()
            if patient:
                dupes = duplicate_lab_orders(patient, tests)
                if dupes:
                    return Response({"detail": "Duplicate order — the same test was ordered in the last 24 hours.", "duplicates": dupes, "code": "duplicate_order"}, status=409)
        return super().create(request, *args, **kwargs)

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        """AAC.3.g/i/l — PDF report; watermarked PROVISIONAL until every
        result is verified (signed) by the pathologist."""
        order = self.get_object()
        pdf = render_lab_report(order)
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{order.order_number or order.pk}.pdf"'
        return resp

    @action(detail=True, methods=["post"])
    def addendum(self, request, pk=None):
        order = self.get_object()
        text = str(request.data.get("text", "")).strip()
        if not text:
            return Response({"text": "Required."}, status=400)
        LabReportAddendum.objects.create(hospital_id=order.hospital_id, lab_order=order, text=text, added_by=request.user)
        return Response({"addenda": list(order.addenda.values("id", "text", "created_at"))})


class SampleWorkflowMixin:
    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        s = self.get_object()
        if s.status == SampleCollection.Status.REJECTED:
            return Response({"detail": "Sample was rejected; collect a new one."}, status=400)
        s.status = SampleCollection.Status.RECEIVED
        s.received_at = timezone.now()
        s.received_by = request.user
        s.save(update_fields=["status", "received_at", "received_by"])
        return Response(self.get_serializer(s).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        s = self.get_object()
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"reason": "A rejection reason is required."}, status=400)
        s.status = SampleCollection.Status.REJECTED
        s.rejection_reason = reason[:40]
        s.rejection_notes = str(request.data.get("notes", ""))[:255]
        s.rejected_at = timezone.now()
        s.save(update_fields=["status", "rejection_reason", "rejection_notes", "rejected_at"])
        order = s.lab_order
        if order.status == LabOrder.Status.SAMPLE_COLLECTED and not order.sample_collections.exclude(status=SampleCollection.Status.REJECTED).exists():
            order.status = LabOrder.Status.ORDERED
            order.save(update_fields=["status"])
        return Response(self.get_serializer(s).data)

    @action(detail=True, methods=["get"])
    def label(self, request, pk=None):
        """AAC.3.e — 50×25 mm barcode label (Code 128)."""
        s = self.get_object()
        from reportlab.graphics.barcode import code128
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(50 * mm, 25 * mm))
        p = s.lab_order.patient
        c.setFont("Helvetica-Bold", 6)
        c.drawString(2 * mm, 21 * mm, f"{p.full_name[:28]}")
        c.setFont("Helvetica", 5.5)
        c.drawString(2 * mm, 18.5 * mm, f"UHID {p.uhid}  {s.sample_type[:14]}")
        code128.Code128(s.barcode, barHeight=9 * mm, barWidth=0.28 * mm).drawOn(c, 1 * mm, 7 * mm)
        c.drawString(2 * mm, 3 * mm, f"{s.barcode}  {timezone.localtime(s.collected_at):%d-%m %H:%M}")
        c.save()
        return HttpResponse(buf.getvalue(), content_type="application/pdf")

    @action(detail=False, methods=["get"])
    def track(self, request):
        """AAC.3.c — look a specimen up by its number/barcode."""
        code = request.query_params.get("barcode", "")
        s = self.get_queryset().filter(barcode=code).select_related("lab_order__patient").first()
        if s is None:
            return Response({"detail": "Not found."}, status=404)
        return Response({**self.get_serializer(s).data, "order_status": s.lab_order.status, "patient": s.lab_order.patient.full_name, "uhid": s.lab_order.patient.uhid})


class LabResultWorkflowMixin:
    def update(self, request, *args, **kwargs):
        from rest_framework.exceptions import ValidationError

        try:
            return super().update(request, *args, **kwargs)
        except ValueError as exc:  # FinalizableModel lock
            raise ValidationError({"detail": str(exc)})

    @action(detail=True, methods=["post"])
    def mark_repeat(self, request, pk=None):
        r = self.get_object()
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response({"reason": "Required."}, status=400)
        LabResult.objects.filter(pk=r.pk).update(needs_repeat=True, repeat_reason=reason[:255])
        r.refresh_from_db()
        return Response(self.get_serializer(r).data)

    @action(detail=True, methods=["post"])
    def amend(self, request, pk=None):
        """Correction to a *signed* result — never a silent edit: the old
        value is kept in core.Amendment, the result is flagged amended
        (counts toward the NABH reporting-errors KPI), and the ordering
        clinician is alerted."""
        r = self.get_object()
        if not r.finalized_at:
            return Response({"detail": "Result isn't signed yet — edit it directly."}, status=400)
        new_value = str(request.data.get("value", "")).strip()
        reason = str(request.data.get("reason", "")).strip()
        if not new_value or not reason:
            return Response({"detail": "value and reason are required."}, status=400)
        with transaction.atomic():
            Amendment.objects.create(
                hospital_id=r.hospital_id, content_type=ContentType.objects.get_for_model(LabResult), object_id=str(r.pk),
                field_name="value", previous_value=r.value, corrected_value=new_value, reason=reason, amended_by=request.user,
            )
            flag = compute_flag(r.lab_test, new_value, r.reference_range) or r.flag
            LabResult.objects.filter(pk=r.pk).update(value=new_value, flag=flag, is_amended=True)
        r.refresh_from_db()
        apply_flag_and_alert(r)
        return Response(self.get_serializer(r).data)


def after_result_saved(result):
    apply_flag_and_alert(result)


def after_order_verified(order):
    notify_report_ready(order)


def render_lab_report(order):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Lab report {order.order_number}")
    p = order.patient
    story = [
        Paragraph(f"<b>{order.hospital.name}</b> — Laboratory Report", styles["Title"]),
        Paragraph(f"Patient: <b>{p.full_name}</b> &nbsp; UHID: {p.uhid} &nbsp; Order: {order.order_number} &nbsp; Ordered: {timezone.localtime(order.ordered_at):%d %b %Y %H:%M}", styles["Normal"]),
        Spacer(1, 8),
    ]
    rows = [["Test", "Result", "Unit", "Reference range", "Flag"]]
    flagged = []
    all_signed = True
    for i, r in enumerate(order.results.select_related("lab_test").order_by("lab_test__name"), start=1):
        all_signed &= r.finalized_at is not None
        rows.append([r.lab_test.name, r.value + (" (amended)" if r.is_amended else ""), r.unit or r.lab_test.unit, r.reference_range or r.lab_test.reference_range, r.get_flag_display()])
        if r.flag != "normal":
            flagged.append(i)
    t = Table(rows, colWidths=[170, 80, 60, 130, 60], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]
    for i in flagged:
        style += [("TEXTCOLOR", (1, i), (1, i), colors.red), ("FONTNAME", (1, i), (1, i), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    story.append(t)
    for a in order.addenda.all():
        story += [Spacer(1, 6), Paragraph(f"<b>Addendum ({timezone.localtime(a.created_at):%d %b %Y %H:%M}):</b> {a.text}", styles["Normal"])]
    story += [Spacer(1, 18), Paragraph("Electronically verified report." if all_signed else "<b>PROVISIONAL — not yet verified by pathologist.</b>", styles["Normal"])]
    doc.build(story)
    return buf.getvalue()


# --- HL7 v2 ORU^R01 analyser import -----------------------------------------------


def parse_hl7_oru(message):
    """Minimal HL7 v2 parser: returns (specimen_id, [(code, value, unit, ref, abnormal_flag)]).
    Specimen id is read from OBR-3 (filler order number) falling back to
    OBR-2 / SPM-2."""
    segments = [s for s in re.split(r"\r\n|\r|\n", message.strip()) if s]
    specimen, results = "", []
    for seg in segments:
        f = seg.split("|")
        if f[0] == "OBR" and not specimen:
            specimen = (f[3] if len(f) > 3 and f[3] else f[2] if len(f) > 2 else "").split("^")[0]
        elif f[0] == "SPM" and not specimen and len(f) > 2:
            specimen = f[2].split("^")[0]
        elif f[0] == "OBX" and len(f) > 5:
            code = f[3].split("^")[0]
            results.append((code, f[5], f[6].split("^")[0] if len(f) > 6 else "", f[7] if len(f) > 7 else "", f[8] if len(f) > 8 else ""))
    return specimen, results


class AnalyzerResultView(APIView):
    """POST raw HL7 (text/plain or {"message": ...}) with X-Analyzer-Token."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        token = request.headers.get("X-Analyzer-Token", "")
        analyzer = LabAnalyzer.objects.filter(api_token=token, is_active=True).exclude(api_token="").first() if token else None
        if analyzer is None:
            return Response({"detail": "Invalid analyser token."}, status=401)
        raw = request.data.get("message") if isinstance(request.data, dict) else None
        raw = raw or request.body.decode("utf-8", errors="ignore")
        specimen, results = parse_hl7_oru(raw)
        sample = SampleCollection.objects.filter(hospital_id=analyzer.hospital_id, barcode=specimen).select_related("lab_order").first()
        if sample is None:
            return Response({"detail": f"Unknown specimen {specimen!r}."}, status=404)
        order = sample.lab_order
        saved = []
        for code, value, unit, ref, _abn in results:
            test_code = analyzer.test_code_map.get(code, code)
            test = order.ordered_tests.filter(code__iexact=test_code).first() or LabTest.objects.filter(hospital_id=analyzer.hospital_id, loinc_code=code).first()
            if test is None:
                continue
            result, _ = LabResult.objects.update_or_create(
                lab_order=order, lab_test=test, finalized_at__isnull=True,
                defaults={"hospital_id": analyzer.hospital_id, "value": value, "unit": unit or test.unit, "reference_range": ref or test.reference_range, "source": "analyzer"},
            )
            after_result_saved(result)
            saved.append(test.name)
        if saved and order.status in (LabOrder.Status.ORDERED, LabOrder.Status.SAMPLE_COLLECTED, LabOrder.Status.PROCESSING):
            order.status = LabOrder.Status.RESULTED
            order.save(update_fields=["status"])
        analyzer.last_message_at = timezone.now()
        analyzer.save(update_fields=["last_message_at"])
        return Response({"specimen": specimen, "order": order.order_number, "results_saved": saved})


class LabReportTemplateViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(LabReportTemplate)
    queryset = LabReportTemplate.objects.all()
    filterset_fields = ["department", "is_active"]


class OutsourcedLabTestViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(OutsourcedLabTest, extra={
        "test_name": serializers.CharField(source="lab_test.name", read_only=True),
        "patient_name": serializers.CharField(source="lab_order.patient.full_name", read_only=True),
    })
    queryset = OutsourcedLabTest.objects.select_related("lab_test", "lab_order__patient")
    filterset_fields = ["status", "external_lab", "lab_order"]

    @action(detail=True, methods=["post"])
    def mark_sent(self, request, pk=None):
        o = self.get_object()
        o.status = OutsourcedLabTest.Status.SENT
        o.sent_at = timezone.now()
        o.external_reference = str(request.data.get("external_reference", o.external_reference))[:80]
        o.save()
        return Response(self.get_serializer(o).data)

    @action(detail=True, methods=["post"])
    def record_result(self, request, pk=None):
        o = self.get_object()
        o.status = OutsourcedLabTest.Status.RESULT_RECEIVED
        o.received_at = timezone.now()
        o.result_text = str(request.data.get("result_text", ""))
        if request.FILES.get("result_file"):
            o.result_file = request.FILES["result_file"]
        o.save()
        return Response(self.get_serializer(o).data)


class LabAnalyzerViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(LabAnalyzer, read_only=("api_token", "last_message_at"))
    queryset = LabAnalyzer.objects.all()

    @action(detail=True, methods=["post"])
    def rotate_token(self, request, pk=None):
        import secrets

        a = self.get_object()
        a.api_token = secrets.token_hex(24)
        a.save(update_fields=["api_token"])
        return Response({"api_token": a.api_token})
