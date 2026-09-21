from datetime import datetime, timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.http import HttpResponse
from . import services
from .models import DailyMISLog
from .serializers import DailyMISLogSerializer


def _parse_window(request):
    """Shared ?start=YYYY-MM-DD&end=YYYY-MM-DD parsing, defaulting to the
    last 7 days so the dashboards have something to show out of the box."""
    end_param = request.query_params.get("end")
    start_param = request.query_params.get("start")

    end = timezone.make_aware(datetime.strptime(end_param, "%Y-%m-%d")) if end_param else timezone.localtime()
    start = timezone.make_aware(datetime.strptime(start_param, "%Y-%m-%d")) if start_param else end - timedelta(days=7)
    return start, end


class BaseReportView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        hospital = request.user.hospital
        start, end = _parse_window(request)
        return Response(self.build_report(hospital, start, end))

    def build_report(self, hospital, start, end):
        raise NotImplementedError


class CallPerformanceView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.call_performance(hospital, start, end)


class EnquiryFunnelView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.enquiry_funnel(hospital, start, end)


class DepartmentDoctorVolumeView(BaseReportView):
    def build_report(self, hospital, start, end):
        return {"rows": services.department_doctor_volume(hospital, start, end)}


class NoShowEffectivenessView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.no_show_recall_effectiveness(hospital, start, end)


class RevenueBySourceView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.revenue_by_source(hospital, start, end)


class DoctorRevenueView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.doctor_revenue(hospital, start, end)


class ReminderDeliverySummaryView(BaseReportView):
    def build_report(self, hospital, start, end):
        return {"rows": services.reminder_delivery_summary(hospital, start, end)}


class DailyMISPreviewView(APIView):
    """Lets the front desk / owner preview MIS for today or any requested window."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        hospital = request.user.hospital
        start_param = request.query_params.get("start")
        end_param = request.query_params.get("end")
        if start_param or end_param:
            start, end = _parse_window(request)
        else:
            start, end = services._today_range()

        summary = services.daily_mis_summary(hospital, start=start, end=end)
        return Response({"summary": summary, "text": services.render_daily_mis_text(hospital, summary)})


class MISExportView(APIView):
    """Export executive MIS report as PDF or CSV."""

    permission_classes = [IsAuthenticated]
    # Per-tenant resource isolation — see apps.integrations.views.
    # DataExportView.throttle_scope and DEFAULT_THROTTLE_RATES["heavy_ops"]
    # in settings.
    throttle_scope = "heavy_ops"

    def perform_content_negotiation(self, request, force=False):
        # Return passthrough renderer so DRF does not raise 404 on ?format=pdf or ?format=csv
        from rest_framework.renderers import BaseRenderer
        return (BaseRenderer(), "*/*")

    def get(self, request):
        import csv
        hospital = request.user.hospital
        start, end = _parse_window(request)
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        summary = services.daily_mis_summary(hospital, start=start, end=end)
        export_format = request.query_params.get("format", "pdf").lower()

        dept_doctor_rows = services.department_doctor_volume(hospital, start, end)
        rev_data = services.revenue_by_source(hospital, start, end)

        if export_format == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="MIS_Report_{start_str}_to_{end_str}.csv"'
            writer = csv.writer(response)
            writer.writerow(["HOSPITAL EXECUTIVE MIS REPORT", hospital.name])
            writer.writerow(["Period", f"{start_str} to {end_str}"])
            writer.writerow([])

            calls = summary.get("calls", {})
            writer.writerow(["TELEPHONY PERFORMANCE"])
            writer.writerow(["Metric", "Value"])
            writer.writerow(["Calls Received", calls.get("received", 0)])
            writer.writerow(["Calls Answered", calls.get("answered", 0)])
            writer.writerow(["Calls Missed", calls.get("missed", 0)])
            writer.writerow(["Pending Callbacks", summary.get("pending_callbacks", 0)])
            writer.writerow([])

            writer.writerow(["DEPARTMENT & DOCTOR CLINICAL FOOTFALL"])
            writer.writerow(["Doctor", "Department", "Booked", "Completed", "No-Show"])
            for r in dept_doctor_rows:
                writer.writerow([r.get("doctor__name", "—"), r.get("doctor__department__name", "—"), r.get("booked", 0), r.get("completed", 0), r.get("no_show", 0)])
            writer.writerow([])

            writer.writerow(["ACQUISITION CHANNEL & REVENUE ATTRIBUTION"])
            writer.writerow(["Source", "Enquiries", "Conversions", "Billed Amount (INR)"])
            for r in rev_data.get("rows", []):
                writer.writerow([r.get("source", ""), r.get("enquiry_count", 0), r.get("conversion_count", 0), r.get("billed_amount", 0)])

            return response

        # Default: PDF
        from .mis_pdf import render_mis_pdf
        pdf_bytes = render_mis_pdf(
            hospital=hospital,
            summary=summary,
            dept_doctor_rows=dept_doctor_rows,
            revenue_rows=rev_data.get("rows", []),
            start_date=start_str,
            end_date=end_str,
            period_label="Executive",
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="MIS_Report_{start_str}_to_{end_str}.pdf"'
        return response


class DailyMISLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DailyMISLogSerializer
    queryset = DailyMISLog.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["report_date"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return DailyMISLog.objects.none()
        return DailyMISLog.objects.filter(hospital_id=self.request.user.hospital_id)
