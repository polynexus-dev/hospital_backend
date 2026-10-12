from django.urls import path
from rest_framework.routers import DefaultRouter

from .frontdesk_views import (
    AdvanceView,
    ApproverListView,
    ConsultationFeeRuleViewSet,
    DiscountApprovalViewSet,
    FrontDeskPolicyView,
    OPDVisitViewSet,
    PatientCardView,
    PreviewView,
    RegistrationView,
)

router = DefaultRouter()
router.register("visits", OPDVisitViewSet, basename="opdvisit")
router.register("discount-approvals", DiscountApprovalViewSet, basename="discountapproval")
router.register("fee-rules", ConsultationFeeRuleViewSet, basename="consultationfeerule")

urlpatterns = router.urls + [
    path("preview/", PreviewView.as_view(), name="frontdesk-preview"),
    path("registrations/", RegistrationView.as_view(), name="frontdesk-register"),
    path("patients/<int:pk>/<str:kind>/", PatientCardView.as_view(), name="frontdesk-patient-print"),
    path("advances/", AdvanceView.as_view(), name="frontdesk-advances"),
    path("approvers/", ApproverListView.as_view(), name="frontdesk-approvers"),
    path("policy/", FrontDeskPolicyView.as_view(), name="frontdesk-policy"),
]
