from rest_framework.routers import DefaultRouter

from .views import GovtSchemeViewSet, SchemeBeneficiaryViewSet, SchemeCaseViewSet, SchemePackageViewSet

router = DefaultRouter()
router.register("schemes", GovtSchemeViewSet, basename="govtscheme")
router.register("packages", SchemePackageViewSet, basename="schemepackage")
router.register("beneficiaries", SchemeBeneficiaryViewSet, basename="schemebeneficiary")
router.register("cases", SchemeCaseViewSet, basename="schemecase")

urlpatterns = router.urls
