from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("incidents", views.SafetyIncidentViewSet, basename="safetyincident")
router.register("medication-errors", views.MedicationErrorViewSet, basename="medicationerror")
router.register("emergency-codes", views.EmergencyCodeViewSet, basename="emergencycode")
router.register("code-activations", views.CodeActivationViewSet, basename="codeactivation")
router.register("mock-drills", views.MockDrillViewSet, basename="mockdrill")
router.register("checklist-templates", views.ChecklistTemplateViewSet, basename="checklisttemplate")
router.register("checklist-runs", views.ChecklistRunViewSet, basename="checklistrun")
router.register("kpi-manual-entries", views.KPIManualEntryViewSet, basename="kpimanualentry")

urlpatterns = router.urls + [
    path("kpis/", views.KPIView.as_view(), name="kpis"),
    path("kpis/publish/", views.KPIPublishView.as_view(), name="kpis-publish"),
]
