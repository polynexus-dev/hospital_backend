from rest_framework.routers import DefaultRouter

from .views import BloodUnitViewSet, CrossMatchRequestViewSet, DonorViewSet, TransfusionViewSet

router = DefaultRouter()
router.register(r"donors", DonorViewSet, basename="donor")
router.register(r"units", BloodUnitViewSet, basename="bloodunit")
router.register(r"cross-matches", CrossMatchRequestViewSet, basename="crossmatchrequest")
router.register(r"transfusions", TransfusionViewSet, basename="transfusion")

urlpatterns = router.urls
