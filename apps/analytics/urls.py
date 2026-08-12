from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CallPerformanceView,
    DailyMISLogViewSet,
    DailyMISPreviewView,
    DepartmentDoctorVolumeView,
    EnquiryFunnelView,
    NoShowEffectivenessView,
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
    path("reports/reminder-delivery/", ReminderDeliverySummaryView.as_view(), name="report-reminder-delivery"),
]
