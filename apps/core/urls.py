from rest_framework.routers import DefaultRouter
from .views import AuditLogViewSet, HospitalViewSet

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("hospitals", HospitalViewSet, basename="hospital")

urlpatterns = router.urls
