from rest_framework.routers import DefaultRouter

from .views import AbhaLinkViewSet, ConsentRequestViewSet, HealthRecordFetchViewSet, NHCXTransactionViewSet

router = DefaultRouter()
router.register(r"abha-links", AbhaLinkViewSet, basename="abhalink")
router.register(r"consent-requests", ConsentRequestViewSet, basename="consentrequest")
router.register(r"health-record-fetches", HealthRecordFetchViewSet, basename="healthrecordfetch")
router.register(r"nhcx-transactions", NHCXTransactionViewSet, basename="nhcxtransaction")

urlpatterns = router.urls
