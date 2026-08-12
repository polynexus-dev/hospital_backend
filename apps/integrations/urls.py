from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import DataExportView, FHIRExportView, HISBillingRecordViewSet, HISVisitViewSet, IntegrationHealthView

router = DefaultRouter()
router.register("his-visits", HISVisitViewSet, basename="hisvisit")
router.register("his-billing", HISBillingRecordViewSet, basename="hisbillingrecord")

urlpatterns = router.urls + [
    path("export/fhir/<str:resource_type>/", FHIRExportView.as_view(), name="fhir-export"),
    path("export/<str:model_name>/", DataExportView.as_view(), name="data-export"),
    path("integration-health/", IntegrationHealthView.as_view(), name="integration-health"),
]

