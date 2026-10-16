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


class LiveDashboardView(APIView):
    """Owner dashboard: live KPIs with previous-period comparison. Unlike the
    other reports, ``end`` is inclusive (end=today includes today)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        start, end = _parse_window(request)
        if request.query_params.get("end"):
            end += timedelta(days=1)
        dept = request.query_params.get("department") or None
        doctor = request.query_params.get("doctor") or None
        return Response(services.live_dashboard(request.user.hospital, start, end, department_id=dept, doctor_id=doctor))


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


class ICUOccupancyView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.icu_occupancy(hospital, start, end)


class OTUtilisationView(BaseReportView):
    def build_report(self, hospital, start, end):
        return services.ot_utilisation(hospital, start, end)


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



class MISScheduleView(APIView):
    """Reports > Scheduled MIS. GET / PUT the schedule; POST sends last
    week's report now, to check the recipients."""

    permission_classes = [IsAuthenticated]
    MANAGERS = ("owner", "admin", "hospital_administrator", "crm_super_admin", "crm_manager", "finance_manager")

    def _allowed(self, user):
        return user.is_superuser or getattr(getattr(user, "role", None), "template", "") in self.MANAGERS

    def _payload(self, schedule):
        from .models import MISReportDelivery

        recent = MISReportDelivery.objects.filter(hospital_id=schedule.hospital_id)[:12]
        return {
            "weekly_enabled": schedule.weekly_enabled, "weekly_day": schedule.weekly_day, "monthly_enabled": schedule.monthly_enabled,
            "email_recipients": schedule.email_recipients, "whatsapp_recipients": schedule.whatsapp_recipients,
            "recent": [{"period": d.period, "start": d.period_start, "end": d.period_end, "sent_at": d.sent_at, "recipients": d.recipients, "error": d.error} for d in recent],
        }

    def _schedule(self, request):
        from .models import MISSchedule

        return MISSchedule.objects.get_or_create(hospital_id=request.user.hospital_id)[0]

    def get(self, request):
        if not self._allowed(request.user):
            return Response({"detail": "Managers only."}, status=403)
        return Response(self._payload(self._schedule(request)))

    def put(self, request):
        import re

        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError as DjValidationError

        if not self._allowed(request.user):
            return Response({"detail": "Managers only."}, status=403)
        s = self._schedule(request)
        d = request.data
        emails = [str(e).strip() for e in (d.get("email_recipients") or []) if str(e).strip()]
        for e in emails:
            try:
                validate_email(e)
            except DjValidationError:
                return Response({"email_recipients": f"'{e}' isn't an email address."}, status=400)
        numbers = []
        for n in d.get("whatsapp_recipients") or []:
            digits = re.sub(r"\D", "", str(n))[-10:]
            if not re.match(r"^[6-9]\d{9}$", digits):
                return Response({"whatsapp_recipients": f"'{n}' isn't a 10-digit mobile number."}, status=400)
            numbers.append(digits)
        day = int(d.get("weekly_day", s.weekly_day))
        if not 0 <= day <= 6:
            return Response({"weekly_day": "0 (Monday) to 6 (Sunday)."}, status=400)
        s.weekly_enabled, s.weekly_day, s.monthly_enabled = bool(d.get("weekly_enabled")), day, bool(d.get("monthly_enabled"))
        s.email_recipients, s.whatsapp_recipients = emails[:20], numbers[:10]
        s.save()
        return Response(self._payload(s))

    def post(self, request):
        from .models import MISReportDelivery
        from .scheduled_mis import headline, previous_week, send_report

        if not self._allowed(request.user):
            return Response({"detail": "Managers only."}, status=403)
        s = self._schedule(request)
        if not (s.email_recipients or s.whatsapp_recipients):
            return Response({"detail": "Add at least one recipient first."}, status=400)
        start, end = previous_week(timezone.localdate())
        # A test send shouldn't stop the real one from going out on schedule.
        MISReportDelivery.objects.filter(hospital_id=s.hospital_id, period="weekly", period_start=start).delete()
        delivery = send_report(s, MISReportDelivery.Period.WEEKLY, start, end)
        MISReportDelivery.objects.filter(pk=delivery.pk).delete()
        return Response({"sent_to": delivery.recipients, "error": delivery.error, "preview": headline(s.hospital, start, end, "Weekly")},
                        status=200 if delivery.sent_at else 502)
