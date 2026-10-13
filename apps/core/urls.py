from django.urls import path
from rest_framework.routers import DefaultRouter

from .location_views import DistrictListView, PinCodeLookupView, PinDirectoryRefreshView, StateListView
from .tenant_public_views import PublicTenantBrandingView
from .views import AuditLogViewSet, DepartmentViewSet, EmergencyAccessLogViewSet, SessionKeyView

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("departments", DepartmentViewSet, basename="department")
router.register("emergency-access-logs", EmergencyAccessLogViewSet, basename="emergencyaccesslog")

urlpatterns = router.urls + [
    path("session-key/", SessionKeyView.as_view(), name="session-key"),
    path("public/tenant-branding/", PublicTenantBrandingView.as_view(), name="tenant-branding"),
    path("locations/states/", StateListView.as_view(), name="location-states"),
    path("locations/districts/", DistrictListView.as_view(), name="location-districts"),
    path("locations/pincode-directory/", PinDirectoryRefreshView.as_view(), name="location-pincode-directory"),
    path("locations/pincode/<str:pincode>/", PinCodeLookupView.as_view(), name="location-pincode"),
]

