from rest_framework.routers import DefaultRouter

from .views import ClaimViewSet, PreAuthRequestViewSet, TPACompanyViewSet

router = DefaultRouter()
router.register("companies", TPACompanyViewSet, basename="tpacompany")
router.register("pre-auth", PreAuthRequestViewSet, basename="preauthrequest")
router.register("claims", ClaimViewSet, basename="claim")

urlpatterns = router.urls
