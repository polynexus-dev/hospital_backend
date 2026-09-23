from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import AuditLogViewSet, HospitalViewSet, session_key_view

router = DefaultRouter()
router.register("audit-logs", AuditLogViewSet, basename="auditlog")
router.register("hospitals", HospitalViewSet, basename="hospital")

urlpatterns = [
    path("session-key/", session_key_view, name="session-key"),
] + router.urls
