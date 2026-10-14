from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("icd10", views.ICD10ViewSet, basename="icd10")
router.register("files", views.MedicalRecordFileViewSet, basename="medicalrecordfile")
router.register("coding", views.CodingRecordViewSet, basename="codingrecord")

from django.urls import path  # noqa: E402

from .reports import MRDReportView  # noqa: E402

urlpatterns = router.urls + [path("reports/", MRDReportView.as_view(), name="mrd-reports")]
