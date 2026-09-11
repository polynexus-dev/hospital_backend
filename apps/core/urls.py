from rest_framework.routers import DefaultRouter

from .views import AuditLogViewSet, EmergencyAccessLogViewSet

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("emergency-access-logs", EmergencyAccessLogViewSet, basename="emergencyaccesslog")

urlpatterns = router.urls
