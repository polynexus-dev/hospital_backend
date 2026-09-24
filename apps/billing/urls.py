from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BedBillingPolicyView, BedChargeRuleViewSet, BillViewSet, InsuranceClaimViewSet, PaymentViewSet

router = DefaultRouter()
router.register(r"bills", BillViewSet, basename="bill")
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"insurance-claims", InsuranceClaimViewSet, basename="insuranceclaim")
router.register(r"bed-charge-rules", BedChargeRuleViewSet, basename="bedchargerule")

urlpatterns = [path("bed-billing-policy/", BedBillingPolicyView.as_view(), name="bed-billing-policy")] + router.urls
