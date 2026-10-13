from django.urls import path

from .online_views import OnlinePaymentLinkViewSet, PaymentGatewayView, RazorpayWebhookView
from rest_framework.routers import DefaultRouter
from .views import BedBillingPolicyView, BedChargeRuleViewSet, BillViewSet, InsuranceClaimViewSet, PaymentViewSet

router = DefaultRouter()
router.register(r"bills", BillViewSet, basename="bill")
router.register(r"payments", PaymentViewSet, basename="payment")
router.register(r"insurance-claims", InsuranceClaimViewSet, basename="insuranceclaim")
router.register(r"bed-charge-rules", BedChargeRuleViewSet, basename="bedchargerule")
router.register(r"payment-links", OnlinePaymentLinkViewSet, basename="onlinepaymentlink")

urlpatterns = [
    path("bed-billing-policy/", BedBillingPolicyView.as_view(), name="bed-billing-policy"),
    path("payment-gateway/", PaymentGatewayView.as_view(), name="payment-gateway"),
    path("payment-webhook/razorpay/<uuid:hospital_id>/", RazorpayWebhookView.as_view(), name="razorpay-webhook"),
] + router.urls
