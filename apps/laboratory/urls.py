from rest_framework.routers import DefaultRouter

from .views import LabOrderViewSet, LabResultViewSet, LabTestPackageViewSet, LabTestViewSet, SampleCollectionViewSet

router = DefaultRouter()
router.register("tests", LabTestViewSet, basename="labtest")
router.register("packages", LabTestPackageViewSet, basename="labtestpackage")
router.register("orders", LabOrderViewSet, basename="laborder")
router.register("samples", SampleCollectionViewSet, basename="samplecollection")
router.register("results", LabResultViewSet, basename="labresult")

urlpatterns = router.urls

from django.urls import path  # noqa: E402

from .workflow import AnalyzerResultView, LabAnalyzerViewSet, LabReportTemplateViewSet, OutsourcedLabTestViewSet  # noqa: E402

router.register("report-templates", LabReportTemplateViewSet, basename="labreporttemplate")
router.register("outsourced-tests", OutsourcedLabTestViewSet, basename="outsourcedlabtest")
router.register("analyzers", LabAnalyzerViewSet, basename="labanalyzer")
urlpatterns = router.urls + [path("analyzer/results/", AnalyzerResultView.as_view(), name="analyzer-results")]
