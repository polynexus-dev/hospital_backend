from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BedOccupancyView,
    CallPerformanceView,
    DailyMISLogViewSet,
    DailyMISPreviewView,
    DepartmentDoctorVolumeView,
    DoctorRevenueView,
    EnquiryFunnelView,
    ICUOccupancyView,
    LabTATView,
    NoShowEffectivenessView,
    OPDSnapshotView,
    OTUtilizationView,
    PharmacyLowStockView,
    ReminderDeliverySummaryView,
    RevenueBySourceView,
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
    path("reports/opd-snapshot/", OPDSnapshotView.as_view(), name="report-opd-snapshot"),
    path("reports/bed-occupancy/", BedOccupancyView.as_view(), name="report-bed-occupancy"),
    path("reports/icu-occupancy/", ICUOccupancyView.as_view(), name="report-icu-occupancy"),
    path("reports/ot-utilization/", OTUtilizationView.as_view(), name="report-ot-utilization"),
    path("reports/lab-tat/", LabTATView.as_view(), name="report-lab-tat"),
    path("reports/pharmacy-low-stock/", PharmacyLowStockView.as_view(), name="report-pharmacy-low-stock"),
]
