from rest_framework.routers import DefaultRouter

from .views import CampRegistrationViewSet, CampaignViewSet, CorporateClientViewSet, HealthPackageViewSet

router = DefaultRouter()
router.register("catalog", HealthPackageViewSet, basename="healthpackage")
router.register("campaigns", CampaignViewSet, basename="campaign")
router.register("registrations", CampRegistrationViewSet, basename="campregistration")
router.register("corporate-clients", CorporateClientViewSet, basename="corporateclient")

urlpatterns = router.urls
