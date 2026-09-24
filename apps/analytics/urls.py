from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CallPerformanceView,
    DailyMISLogViewSet,
    DailyMISPreviewView,
    DepartmentDoctorVolumeView,
    DoctorRevenueView,
    EnquiryFunnelView,
    NoShowEffectivenessView,
    ReminderDeliverySummaryView,
    RevenueBySourceView,
    MISExportView,
)

router = DefaultRouter()
router.register("mis-logs", DailyMISLogViewSet, basename="dailymislog")

urlpatterns = router.urls + [
    path("reports/call-performance/", CallPerformanceView.as_view(), name="report-call-performance"),
    path("reports/enquiry-funnel/", EnquiryFunnelView.as_view(), name="report-enquiry-funnel"),
    path("reports/department-doctor-volume/", DepartmentDoctorVolumeView.as_view(), name="report-department-doctor-volume"),
    path("reports/no-show-effectiveness/", NoShowEffectivenessView.as_view(), name="report-no-show-effectiveness"),
    path("reports/daily-mis-preview/", DailyMISPreviewView.as_view(), name="report-daily-mis-preview"),
    path("reports/revenue-by-source/", RevenueBySourceView.as_view(), name="report-revenue-by-source"),
    path("reports/doctor-revenue/", DoctorRevenueView.as_view(), name="report-doctor-revenue"),
    path("reports/reminder-delivery/", ReminderDeliverySummaryView.as_view(), name="report-reminder-delivery"),
    path("reports/mis-export/", MISExportView.as_view(), name="report-mis-export"),
]

from .predictive import (  # noqa: E402
    AdmissionsForecastView,
    BedForecastView,
    NoShowRiskView,
    OPDFootfallForecastView,
    StaffingForecastView,
    StockoutForecastView,
)

urlpatterns += [
    path("predict/opd-footfall/", OPDFootfallForecastView.as_view(), name="predict-opd"),
    path("predict/admissions/", AdmissionsForecastView.as_view(), name="predict-admissions"),
    path("predict/beds/", BedForecastView.as_view(), name="predict-beds"),
    path("predict/staffing/", StaffingForecastView.as_view(), name="predict-staffing"),
    path("predict/stockouts/", StockoutForecastView.as_view(), name="predict-stockouts"),
    path("predict/no-show/", NoShowRiskView.as_view(), name="predict-no-show"),
]
