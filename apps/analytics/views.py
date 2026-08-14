from datetime import datetime, timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    """Lets the front desk / owner preview today's MIS without waiting for
    the Celery beat schedule to fire."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        summary = services.daily_mis_summary(request.user.hospital)
        return Response({"summary": summary, "text": services.render_daily_mis_text(request.user.hospital, summary)})


class OPDSnapshotView(APIView):
    """ERP ops dashboard, OPD metrics only (docs/erp/06-navigation-and-dashboards.md
    §4) — today's counts, not a date-range report like the CRM views
    above, so it doesn't extend BaseReportView."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.opd_snapshot(request.user.hospital))


class BedOccupancyView(APIView):
    """ERP ops dashboard, bed occupancy (Phase 4 addition — see
    OPDSnapshotView above for the same "today, not a date-range report"
    reasoning)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.bed_occupancy_snapshot(request.user.hospital))


class ICUOccupancyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.icu_occupancy_snapshot(request.user.hospital))


class OTUtilizationView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.ot_utilization_snapshot(request.user.hospital))


class LabTATView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.lab_tat_snapshot(request.user.hospital))


class PharmacyLowStockView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.pharmacy_low_stock_snapshot(request.user.hospital))


class DailyMISLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DailyMISLogSerializer
    queryset = DailyMISLog.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["report_date"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return DailyMISLog.objects.none()
        return DailyMISLog.objects.filter(hospital_id=self.request.user.hospital_id)
