from rest_framework.routers import DefaultRouter

from .views import PreAuthRequestViewSet, TPACompanyViewSet

router = DefaultRouter()
router.register("companies", TPACompanyViewSet, basename="tpacompany")
router.register("pre-auth", PreAuthRequestViewSet, basename="preauthrequest")

urlpatterns = router.urls
