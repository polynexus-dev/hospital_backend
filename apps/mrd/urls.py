from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("icd10", views.ICD10ViewSet, basename="icd10")
router.register("files", views.MedicalRecordFileViewSet, basename="medicalrecordfile")
router.register("coding", views.CodingRecordViewSet, basename="codingrecord")

urlpatterns = router.urls
