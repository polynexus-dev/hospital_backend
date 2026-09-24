import io
from datetime import date, timedelta
from statistics import median

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantModelSerializer

from .models import AIR_KERMA_FOLLOW_UP_MGY, D2B_TARGET_MINUTES, CathDevice, CathProcedure

COMPLICATIONS = [
    "Access-site haematoma", "Retroperitoneal bleed", "Coronary dissection", "No-reflow", "Perforation", "Arrhythmia requiring treatment",
    "Contrast reaction", "Contrast nephropathy", "Stroke / TIA", "Emergency CABG", "Death",
]
CHARGE_SOURCE = "cathlab"


class CathDeviceSerializer(TenantModelSerializer):
    class Meta:
        model = CathDevice
        fields = "__all__"


class CathProcedureSerializer(TenantModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    operator_name = serializers.CharField(source="operator.name", read_only=True)
    devices = CathDeviceSerializer(many=True, read_only=True)
    door_to_device_minutes = serializers.IntegerField(read_only=True)
    max_contrast_ml = serializers.IntegerField(read_only=True)
    safety_flags = serializers.ListField(read_only=True)

    class Meta:
        model = CathProcedure
        fields = "__all__"
        read_only_fields = ["status", "started_at", "device_at", "ended_at", "finalized_at", "finalized_by"]

    def validate_vessel_findings(self, value):
        for f in value:
            if not isinstance(f, dict) or not f.get("vessel"):
                raise serializers.ValidationError("Each finding needs a vessel.")
            sten = f.get("stenosis_percent")
            if sten is not None and not (isinstance(sten, (int, float)) and 0 <= sten <= 100):
                raise serializers.ValidationError(f"{f['vessel']}: stenosis must be 0–100 %.")
            timi = f.get("timi_flow")
            if timi is not None and timi not in (0, 1, 2, 3):
                raise serializers.ValidationError(f"{f['vessel']}: TIMI flow is 0–3.")
        return value

    def validate_complications(self, value):
        unknown = [c for c in value if c not in COMPLICATIONS and c != "None"]
        if unknown:
            raise serializers.ValidationError(f"Unknown: {', '.join(unknown)}. Choose from: {', '.join(COMPLICATIONS)}")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        patient = attrs.get("patient") or getattr(self.instance, "patient", None)
        for link in ("admission", "ed_visit"):
            obj = attrs.get(link)
            if obj is not None and obj.patient_id != patient.pk:
                raise serializers.ValidationError({link: "Belongs to a different patient."})
        return attrs


class CathProcedureViewSet(ClinicalCRUDViewSet):
    serializer_class = CathProcedureSerializer
    queryset = CathProcedure.objects.select_related("patient", "operator", "admission", "ed_visit").prefetch_related("devices")
    filterset_fields = ["patient", "admission", "procedure_type", "urgency", "status", "operator"]
    search_fields = ["patient__first_name", "patient__last_name", "indication"]
    audited_fields = ("status", "contrast_ml", "air_kerma_mgy", "vessel_findings", "complications", "conclusion", "finalized_at")
    action_permissions = {name: "cathlab.change_cathprocedure" for name in ("start", "reperfusion", "complete", "finalize", "add_device", "post_charges")}
    action_permissions.update({"report": "cathlab.view_cathprocedure", "kpis": "cathlab.view_cathprocedure"})

    def perform_update(self, serializer):
        if serializer.instance.finalized_at:
            raise serializers.ValidationError({"detail": "The report is finalised and locked."})
        super().perform_update(serializer)

    def _state(self, proc, allowed):
        if proc.status not in allowed:
            raise serializers.ValidationError({"status": f"Not allowed while {proc.get_status_display().lower()}."})

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Sheath in. Door time defaults to the ED arrival for STEMI patients."""
        proc = self.get_object()
        self._state(proc, [CathProcedure.Status.SCHEDULED])
        proc.status, proc.started_at = CathProcedure.Status.IN_PROGRESS, timezone.now()
        if not proc.door_at and proc.ed_visit_id:
            proc.door_at = proc.ed_visit.arrived_at
        proc.save()
        self._log("update", proc)
        return Response(self.get_serializer(proc).data)

    @action(detail=True, methods=["post"])
    def reperfusion(self, request, pk=None):
        """First balloon / device — stops the door-to-device clock."""
        proc = self.get_object()
        self._state(proc, [CathProcedure.Status.IN_PROGRESS])
        proc.device_at = timezone.now()
        proc.save(update_fields=["device_at"])
        return Response(self.get_serializer(proc).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        """POST {abandoned?: true} — sheath out."""
        proc = self.get_object()
        self._state(proc, [CathProcedure.Status.IN_PROGRESS])
        proc.ended_at = timezone.now()
        proc.status = CathProcedure.Status.ABANDONED if request.data.get("abandoned") else CathProcedure.Status.COMPLETED
        proc.save()
        self._log("update", proc)
        return Response(self.get_serializer(proc).data)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        """Locks the report. Needs a conclusion, contrast and fluoroscopy figures, and — for a primary PCI
        over the door-to-device target — the reason for the delay."""
        proc = self.get_object()
        self._state(proc, [CathProcedure.Status.COMPLETED, CathProcedure.Status.ABANDONED])
        if proc.finalized_at:
            return Response({"detail": "Already finalised."}, status=status.HTTP_400_BAD_REQUEST)
        if not proc.conclusion.strip():
            return Response({"conclusion": "Write the conclusion before finalising."}, status=status.HTTP_400_BAD_REQUEST)
        if proc.contrast_ml is None or proc.fluoro_minutes is None:
            return Response({"detail": "Record contrast volume and fluoroscopy time before finalising."}, status=status.HTTP_400_BAD_REQUEST)
        d2b = proc.door_to_device_minutes
        if proc.procedure_type == CathProcedure.Type.PRIMARY_PCI and d2b is not None and d2b > D2B_TARGET_MINUTES and not proc.delay_reason.strip():
            return Response({"delay_reason": f"Door-to-device was {d2b} min (target {D2B_TARGET_MINUTES}) — record the reason for the delay."},
                            status=status.HTTP_400_BAD_REQUEST)
        proc.finalized_at, proc.finalized_by = timezone.now(), request.user
        proc.save(update_fields=["finalized_at", "finalized_by"])
        self._log("update", proc)
        return Response(self.get_serializer(proc).data)

    @action(detail=True, methods=["post"], url_path="add-device")
    def add_device(self, request, pk=None):
        """POST {kind, brand, lot_number, size?, udi?, serial_number?, vessel?, quantity?, unit_price?}"""
        proc = self.get_object()
        if proc.finalized_at:
            return Response({"detail": "The report is finalised and locked."}, status=status.HTTP_400_BAD_REQUEST)
        ser = CathDeviceSerializer(data={**request.data, "procedure": proc.pk}, context={"request": request})
        ser.is_valid(raise_exception=True)
        ser.save(hospital_id=proc.hospital_id)
        return Response(self.get_serializer(proc).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="post-charges")
    @transaction.atomic
    def post_charges(self, request, pk=None):
        """Procedure fee (credited to the operator for payouts) + every device, on the admission's running bill. Idempotent."""
        from apps.billing.bed_charges import running_bill
        from apps.billing.models import BillItem
        from apps.billing.services import recalculate_bill

        proc = self.get_object()
        if proc.admission_id is None:
            return Response({"admission": "Link the procedure to an admission to bill it."}, status=status.HTTP_400_BAD_REQUEST)
        bill = running_bill(proc.admission)
        ref = f"cath:{proc.pk}"
        BillItem.objects.filter(bill=bill, source=CHARGE_SOURCE, source_ref=ref).delete()
        day = timezone.localdate(proc.started_at or proc.scheduled_at or timezone.now())
        if proc.procedure_tariff:
            t = proc.procedure_tariff
            BillItem.objects.create(bill=bill, description=f"{t.name} ({proc.get_procedure_type_display()})", quantity=1,
                                    unit_price=t.rate_for(bill.patient_category), total_price=0, tariff=t, hsn_sac=t.hsn_sac, gst_rate=t.gst_rate,
                                    doctor=proc.operator, service_date=day, source=CHARGE_SOURCE, source_ref=ref)
        for d in proc.devices.all():
            BillItem.objects.create(bill=bill, description=f"{d.get_kind_display()} {d.brand} {d.size} (lot {d.lot_number})".strip(),
                                    quantity=d.quantity, unit_price=d.unit_price, total_price=0, service_date=day, source=CHARGE_SOURCE, source_ref=ref)
        recalculate_bill(bill)
        from apps.schemes.services import reapply_for_admission

        reapply_for_admission(proc.admission)
        bill.refresh_from_db()
        return Response({"bill": bill.pk, "bill_number": bill.bill_number, "net_amount": bill.net_amount,
                         "lines": bill.items.filter(source=CHARGE_SOURCE, source_ref=ref).count()})

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        proc = self.get_object()
        resp = HttpResponse(_report_pdf(proc), content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="cath-report-{proc.pk}.pdf"'
        return resp

    @action(detail=False, methods=["get"])
    def kpis(self, request):
        """GET ?start=&end= — volumes, door-to-device for primary PCI, complications, radial access, contrast & radiation."""
        try:
            end = date.fromisoformat(request.query_params["end"]) if request.query_params.get("end") else timezone.localdate()
            start = date.fromisoformat(request.query_params["start"]) if request.query_params.get("start") else end - timedelta(days=90)
        except ValueError:
            return Response({"detail": "start/end must be YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        procs = [p for p in self.get_queryset().filter(status=CathProcedure.Status.COMPLETED, started_at__date__range=(start, end))]
        ppci = [p.door_to_device_minutes for p in procs if p.procedure_type == CathProcedure.Type.PRIMARY_PCI and p.door_to_device_minutes is not None]
        with_access = [p for p in procs if p.access_site]
        contrast = [p.contrast_ml for p in procs if p.contrast_ml]
        by_type = {}
        for p in procs:
            by_type[p.get_procedure_type_display()] = by_type.get(p.get_procedure_type_display(), 0) + 1
        return Response({
            "start": start, "end": end, "procedures": len(procs), "by_type": by_type,
            "primary_pci": {"count": len(ppci), "median_door_to_device_minutes": median(ppci) if ppci else None,
                            "within_target_percent": round(100 * sum(1 for m in ppci if m <= D2B_TARGET_MINUTES) / len(ppci), 1) if ppci else None,
                            "target_minutes": D2B_TARGET_MINUTES},
            "complication_rate_percent": round(100 * sum(1 for p in procs if [c for c in p.complications if c != "None"]) / len(procs), 1) if procs else None,
            "radial_access_percent": round(100 * sum(1 for p in with_access if "radial" in p.access_site.lower()) / len(with_access), 1) if with_access else None,
            "mean_contrast_ml": round(sum(contrast) / len(contrast)) if contrast else None,
            "contrast_over_limit": sum(1 for p in procs if p.max_contrast_ml and p.contrast_ml and p.contrast_ml > p.max_contrast_ml),
            "high_radiation_dose": sum(1 for p in procs if (p.air_kerma_mgy or 0) >= AIR_KERMA_FOLLOW_UP_MGY),
        })


def _report_pdf(proc):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

    st = getSampleStyleSheet()
    p = proc.patient
    rows = [
        ["Patient", f"{p.full_name} ({p.uhid})", "Procedure", proc.get_procedure_type_display()],
        ["Operator", f"Dr. {proc.operator.name}", "Urgency", proc.get_urgency_display()],
        ["Date", proc.started_at.strftime("%d %b %Y %H:%M") if proc.started_at else "—", "Access", f"{proc.access_site or '—'} {proc.sheath_fr or ''}F"],
        ["Contrast", f"{proc.contrast_ml or '—'} mL {proc.contrast_agent}", "Fluoroscopy", f"{proc.fluoro_minutes or '—'} min"],
        ["Air kerma", f"{proc.air_kerma_mgy or '—'} mGy", "DAP", f"{proc.dap_gy_cm2 or '—'} Gy·cm²"],
        ["Dominance", proc.dominance or "—", "LV EF", f"{proc.lv_ef_percent or '—'} %"],
    ]
    if proc.door_to_device_minutes is not None:
        rows.append(["Door-to-device", f"{proc.door_to_device_minutes} min", "Target", f"≤ {D2B_TARGET_MINUTES} min"])
    story = [Paragraph(f"{proc.hospital.name} — Cath lab report", st["Title"]), Spacer(1, 3 * mm), Table(rows, colWidths=[30 * mm, 60 * mm, 30 * mm, 60 * mm])]
    if proc.vessel_findings:
        story += [Paragraph("Findings", st["Heading3"]), Table([["Vessel", "Segment", "Stenosis", "TIMI", "Intervention"]] + [
            [f.get("vessel", ""), f.get("segment", ""), f"{f.get('stenosis_percent', '—')} %", str(f.get("timi_flow", "—")), f.get("intervention", "")]
            for f in proc.vessel_findings])]
    devices = list(proc.devices.all())
    if devices:
        story += [Paragraph("Devices implanted", st["Heading3"]), Table([["Device", "Brand / model", "Size", "Lot", "UDI", "Vessel"]] + [
            [d.get_kind_display(), f"{d.brand} {d.model_name}".strip(), d.size, d.lot_number, d.udi, d.vessel] for d in devices])]
    story += [Paragraph("Complications", st["Heading3"]), Paragraph(", ".join(proc.complications) or "None", st["Normal"]),
              Paragraph("Conclusion", st["Heading3"]), Paragraph(proc.conclusion or "—", st["Normal"]),
              Paragraph(f"Recommendation: {proc.recommendation or '—'}", st["Normal"])]
    if proc.delay_reason:
        story.append(Paragraph(f"Reason for door-to-device delay: {proc.delay_reason}", st["Normal"]))
    for flag in proc.safety_flags:
        story.append(Paragraph(f"⚠ {flag}", st["Normal"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Finalised" if proc.finalized_at else "DRAFT — not finalised", st["Italic"]))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm).build(story)
    return buf.getvalue()
