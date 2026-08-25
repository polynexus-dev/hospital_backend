from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    PlatformAnalyticsView,
    SaaSSupportTicketViewSet,
    SupportTicketViewSet,
    TenantInvoiceViewSet,
    TenantSubscriptionViewSet,
    TenantUsageSnapshotViewSet,
)

router = DefaultRouter()
router.register("saas-admin/subscriptions", TenantSubscriptionViewSet, basename="tenantsubscription")
router.register("saas-admin/invoices", TenantInvoiceViewSet, basename="tenantinvoice")
router.register("saas-admin/usage-snapshots", TenantUsageSnapshotViewSet, basename="tenantusagesnapshot")
router.register("saas-admin/tickets", SaaSSupportTicketViewSet, basename="saas-support-ticket")
router.register("support-tickets", SupportTicketViewSet, basename="support-ticket")

urlpatterns = router.urls + [
    path("saas-admin/analytics/", PlatformAnalyticsView.as_view(), name="saas-admin-platform-analytics"),
]
