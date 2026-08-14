from rest_framework.routers import DefaultRouter
from .views import BillViewSet, InsuranceClaimViewSet, PaymentViewSet

router = DefaultRouter()
router.register(r"bills", BillViewSet, basename="bill")
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"insurance-claims", InsuranceClaimViewSet, basename="insuranceclaim")

urlpatterns = router.urls
