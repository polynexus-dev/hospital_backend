from rest_framework.routers import DefaultRouter

from .views import LabOrderViewSet, LabResultViewSet, LabTestPackageViewSet, LabTestViewSet, SampleCollectionViewSet

router = DefaultRouter()
router.register("tests", LabTestViewSet, basename="labtest")
router.register("packages", LabTestPackageViewSet, basename="labtestpackage")
router.register("orders", LabOrderViewSet, basename="laborder")
router.register("samples", SampleCollectionViewSet, basename="samplecollection")
router.register("results", LabResultViewSet, basename="labresult")

urlpatterns = router.urls
