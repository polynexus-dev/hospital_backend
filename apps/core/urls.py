from django.urls import path
from rest_framework.routers import DefaultRouter

from .tenant_public_views import PublicTenantBrandingView
from .views import AuditLogViewSet, DepartmentViewSet, EmergencyAccessLogViewSet, SessionKeyView

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("departments", DepartmentViewSet, basename="department")
router.register("emergency-access-logs", EmergencyAccessLogViewSet, basename="emergencyaccesslog")

urlpatterns = router.urls + [
    path("session-key/", SessionKeyView.as_view(), name="session-key"),
    path("public/tenant-branding/", PublicTenantBrandingView.as_view(), name="tenant-branding"),
]

