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
from apps.core.modules import require_module

from .models import LabAnalyzer, LabOrder, LabReportAddendum, LabReportTemplate, LabResult, LabSettings, LabTest, OutsourcedLabTest, SampleCollection

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
        if patient_id:
            from apps.billing.adjustments import ensure_patient_open
            from apps.patients.models import Patient

            ensure_patient_open(Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first(),
                                "order lab tests", request.data.get("priority", ""))
        tests = [int(t) for t in (request.data.get("ordered_tests") or []) if str(t).isdigit()]
        if patient_id and tests and not request.data.get("confirm_duplicate"):
            from apps.patients.models import Patient

            patient = Patient.objects.filter(pk=patient_id, hospital_id=request.user.hospital_id).first()
            if patient:
                dupes = duplicate_lab_orders(patient, tests)
                if dupes:
                    return Response({"detail": "Duplicate order — the same test was ordered in the last 24 hours.", "duplicates": dupes, "code": "duplicate_order"}, status=409)
        lab_settings = LabSettings.for_hospital(request.user.hospital_id)
        if not (lab_settings.merge_window_minutes or lab_settings.split_by_department):
            return super().create(request, *args, **kwargs)
        # Department-wise orders / merge into a recent open order.
        from .lab_extras import place_order

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        patient, chosen = data.pop("patient"), data.pop("ordered_tests", [])
        if not chosen:
            return super().create(request, *args, **kwargs)
        placed = place_order(hospital_id=request.user.hospital_id, user=request.user, patient=patient, tests=chosen, fields=data)
        for order, merged in placed:
            self._log("update" if merged else "create", order)
        body = self.get_serializer(placed[0][0]).data
        body["orders"] = [{"id": o.pk, "order_number": o.order_number, "department": o.department, "merged": m} for o, m in placed]
        body["merged"] = any(m for _, m in placed)
        return Response(body, status=201)

    def get_queryset(self):
        from .lab_extras import PROCESSING_STATES, filter_processing, with_processing_counts

        qs = super().get_queryset()
        dept = self.request.query_params.get("department")
        if dept:
            qs = qs.filter(ordered_tests__department=dept).distinct()
        state = self.request.query_params.get("processing")
        if state in PROCESSING_STATES:
            qs = filter_processing(qs, state)
        elif getattr(self, "action", None) == "list":
            qs = with_processing_counts(qs)  # processing_state without a query per order
        return qs

    @action(detail=False, methods=["get"], url_path="patient-report")
    def patient_report(self, request):
        """GET ?patient=<id>[&orders=1,2] — one merged PDF of the patient's
        verified orders (all of them, or the ones listed), by department."""
        from django.http import HttpResponse

        from .merged_report import render_patient_lab_report

        qs = self.get_queryset().filter(patient_id=request.query_params.get("patient") or 0, status=LabOrder.Status.VERIFIED)
        wanted = [int(x) for x in str(request.query_params.get("orders", "")).split(",") if x.strip().isdigit()]
        if wanted:
            qs = qs.filter(pk__in=wanted)
        orders = list(qs.select_related("patient", "hospital").distinct())
        if not orders:
            return Response({"detail": "No verified reports for this patient."}, status=404)
        resp = HttpResponse(render_patient_lab_report(orders), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="Lab_report_{orders[0].patient.uhid}.pdf"'
        return resp

    @action(detail=False, methods=["get"], url_path="patient-status")
    def patient_status(self, request):
        """GET ?patient=<id> — the patient's pending investigations with each
        one's stage, and the reporting section (MedNet "Pending investigations")."""
        from apps.patients.models import Patient

        from .lab_extras import patient_lab_status

        patient = Patient.objects.filter(pk=request.query_params.get("patient") or 0, hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"patient": "Unknown patient."}, status=400)
        body = patient_lab_status(request.user.hospital_id, patient.pk)
        body["patient"] = {"id": patient.pk, "name": patient.full_name, "uhid": patient.uhid}
        return Response(body)

    @action(detail=False, methods=["get"])
    def worklist(self, request):
        """GET ?department=&status=&priority=&date_from=&date_to= — one row per
        test across orders (the investigation view)."""
        from .lab_extras import worklist

        q = request.query_params
        return Response(worklist(request.user.hospital_id, department=q.get("department", ""), status=q.get("status", ""),
                                 priority=q.get("priority", ""), date_from=q.get("date_from") or None, date_to=q.get("date_to") or None,
                                 patient=q.get("patient") or None))

    @action(detail=True, methods=["post"])
    def dispatch_report(self, request, pk=None):
        """POST {channels: ["email", "whatsapp"]} — send the verified report now."""
        from .lab_extras import send_reports

        order = self.get_object()
        if order.status != LabOrder.Status.VERIFIED:
            return Response({"detail": "Only a verified report can be sent."}, status=400)
        channels = request.data.get("channels") or LabSettings.for_hospital(order.hospital_id).dispatch_channels or ["whatsapp"]
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",") if c.strip()]
        if not channels or set(channels) - {"email", "whatsapp"}:
            return Response({"channels": "email and/or whatsapp."}, status=400)
        rows = send_reports([order], channels, user=request.user)
        return Response([{"channel": r.channel, "recipient": r.recipient, "sent": r.sent, "error": r.error} for r in rows])

    @action(detail=True, methods=["get"])
    def dispatches(self, request, pk=None):
        order = self.get_object()
        return Response(list(order.dispatches.values("id", "channel", "recipient", "sent", "error", "automatic", "created_at")))

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
        from .lab_extras import recompute_calculated

        recompute_calculated(r.lab_order)
        return Response(self.get_serializer(r).data)


def after_result_saved(result):
    from .lab_extras import recompute_calculated

    apply_flag_and_alert(result)
    recompute_calculated(result.lab_order)


def after_order_verified(order):
    from .lab_extras import schedule_dispatch

    notify_report_ready(order)
    schedule_dispatch(order)


def render_lab_report(order):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Lab report {order.order_number}")
    p = order.patient
    results = list(order.results.select_related("lab_test").order_by("lab_test__name"))
    # NABL: the mark and certificate number appear only on a report that
    # has at least one test within the scope of accreditation, and each such
    # test is marked; tests outside the scope are identified as such.
    lab_settings = LabSettings.for_hospital(order.hospital_id)
    nabl = lab_settings.nabl_active and any(r.lab_test.is_nabl_accredited for r in results)
    story = []
    if nabl and lab_settings.nabl_logo:
        try:
            from reportlab.platypus import Image

            story.append(Image(lab_settings.nabl_logo.path, width=40, height=40, hAlign="RIGHT"))
        except Exception:  # noqa: BLE001 - a missing logo file must not stop the report
            pass
    story += [
        Paragraph(f"<b>{order.hospital.name}</b> — Laboratory Report", styles["Title"]),
        Paragraph(f"Patient: <b>{p.full_name}</b> &nbsp; UHID: {p.uhid} &nbsp; Order: {order.order_number} &nbsp; Ordered: {timezone.localtime(order.ordered_at):%d %b %Y %H:%M}", styles["Normal"]),
        Spacer(1, 8),
    ]
    if nabl:
        story.insert(-1, Paragraph(f"NABL accredited laboratory &middot; Certificate No. {lab_settings.nabl_certificate_number}", styles["Normal"]))
    rows = [["Test", "Result", "Unit", "Reference range", "Flag"]]
    flagged = []
    all_signed = True
    for i, r in enumerate(results, start=1):
        all_signed &= r.finalized_at is not None
        name = r.lab_test.name + (" *" if nabl and r.lab_test.is_nabl_accredited else "") + (" (calculated)" if r.source == "calculated" else "")
        rows.append([name, r.value + (" (amended)" if r.is_amended else ""), r.unit or r.lab_test.unit, r.reference_range or r.lab_test.reference_range, r.get_flag_display()])
        if r.flag != "normal":
            flagged.append(i)
    t = Table(rows, colWidths=[170, 80, 60, 130, 60], repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]
    for i in flagged:
        style += [("TEXTCOLOR", (1, i), (1, i), colors.red), ("FONTNAME", (1, i), (1, i), "Helvetica-Bold")]
    t.setStyle(TableStyle(style))
    story.append(t)
    if nabl:
        story += [Spacer(1, 4), Paragraph(f"* Within the scope of NABL accreditation (Certificate No. {lab_settings.nabl_certificate_number}). "
                                          "Tests without * are outside the scope of accreditation.", styles["Italic"])]
    for a in order.addenda.all():
        story += [Spacer(1, 6), Paragraph(f"<b>Addendum ({timezone.localtime(a.created_at):%d %b %Y %H:%M}):</b> {a.text}", styles["Normal"])]
    story += [Spacer(1, 18), Paragraph("Electronically verified report." if all_signed else "<b>PROVISIONAL — not yet verified by pathologist.</b>", styles["Normal"])]
    signers = [r.finalized_by for r in results if r.finalized_by_id]
    if all_signed and signers:
        from apps.clinical.sign_block import signature_block

        designation = next((r.lab_test.report_template.signatory_designation for r in results if r.lab_test.report_template_id), "Consultant Pathologist")
        block = signature_block(signers[-1], subtitle=designation)
        if block is not None:
            story += [Spacer(1, 8), block]
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
        require_module(analyzer.hospital, "laboratory")
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


def finalize_outside_report(outsourced, user):
    """The outside report stands as the result: one finalised LabResult for
    the test pointing at the attached report; verifies the order if nothing
    else is open."""
    order, test = outsourced.lab_order, outsourced.lab_test
    result = LabResult.objects.filter(lab_order=order, lab_test=test, finalized_at__isnull=True).first()
    if result is None and not LabResult.objects.filter(lab_order=order, lab_test=test, finalized_at__isnull=False).exists():
        result = LabResult.objects.create(hospital_id=order.hospital_id, lab_order=order, lab_test=test, value="See outside report",
                                          unit=test.unit, reference_range=test.reference_range, entered_by=user)
    if result is not None:
        result.finalize(user)
    open_tests = [t for t in order.ordered_tests.all() if not order.results.filter(lab_test=t, finalized_at__isnull=False).exists()]
    if not open_tests and order.status != LabOrder.Status.VERIFIED:
        order.status = LabOrder.Status.VERIFIED
        order.save(update_fields=["status"])
        after_order_verified(order)


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
        """Records the external lab's result. With report type "outside report"
        and the report file attached, the test is finalised at once (the
        external lab's PDF is the report) and the order is verified when it
        was the last open test."""
        o = self.get_object()
        o.status = OutsourcedLabTest.Status.RESULT_RECEIVED
        o.received_at = timezone.now()
        o.result_text = str(request.data.get("result_text", ""))
        if request.data.get("report_type") in OutsourcedLabTest.ReportType.values:
            o.report_type = request.data["report_type"]
        if request.FILES.get("result_file"):
            o.result_file = request.FILES["result_file"]
        if o.report_type == OutsourcedLabTest.ReportType.OUTSIDE_REPORT and not o.result_file:
            return Response({"result_file": "Attach the external lab's report to finalise an outside report."}, status=400)
        o.save()
        if o.report_type == OutsourcedLabTest.ReportType.OUTSIDE_REPORT:
            finalize_outside_report(o, request.user)
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


class LabSettingsView(APIView):
    """GET/PUT /laboratory/settings/ — NABL, report dispatch, order grouping."""

    FIELDS = ("nabl_certificate_number", "nabl_valid_until", "auto_dispatch", "dispatch_channels", "dispatch_times",
              "dispatch_delay_minutes", "merge_window_minutes", "split_by_department", "dispatch_outsourced", "merge_dispatch_reports")

    def _payload(self, s):
        data = {f: getattr(s, f) for f in self.FIELDS}
        data.update(nabl_logo=s.nabl_logo.url if s.nabl_logo else "", nabl_active=s.nabl_active)
        return data

    def get(self, request):
        if not request.user.has_perm("laboratory.view_labsettings"):
            return Response({"detail": "You don't have access to lab settings."}, status=403)
        return Response(self._payload(LabSettings.for_hospital(request.user.hospital_id)))

    def put(self, request):
        if not request.user.has_perm("laboratory.change_labsettings"):
            return Response({"detail": "You don't have access to change lab settings."}, status=403)
        s = LabSettings.for_hospital(request.user.hospital_id)
        ser = LabSettingsSerializer(s, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        if request.FILES.get("nabl_logo"):
            s.nabl_logo = request.FILES["nabl_logo"]
            s.save(update_fields=["nabl_logo"])
        return Response(self._payload(s))


class LabSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabSettings
        fields = LabSettingsView.FIELDS

    def validate_dispatch_channels(self, value):
        if not isinstance(value, list) or set(value) - {"email", "whatsapp"}:
            raise serializers.ValidationError('A list of "email" and/or "whatsapp".')
        return value

    def validate_dispatch_times(self, value):
        if not isinstance(value, list) or not all(isinstance(t, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in value):
            raise serializers.ValidationError('A list of times like "10:00".')
        return sorted(set(value))


class LabReportLinkView(APIView):
    """The signed link in a WhatsApp report message (valid 30 days)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        from .lab_extras import order_from_token

        order = order_from_token(token)
        if order is None:
            return HttpResponse("This link has expired. Please ask the hospital for your report.", status=404, content_type="text/plain")
        resp = HttpResponse(render_lab_report(order), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="Lab_report_{order.order_number}.pdf"'
        return resp
