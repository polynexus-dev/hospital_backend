from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AuditLogViewSet, EmergencyAccessLogViewSet, SessionKeyView

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("emergency-access-logs", EmergencyAccessLogViewSet, basename="emergencyaccesslog")

urlpatterns = router.urls + [
    path("session-key/", SessionKeyView.as_view(), name="session-key"),
]
