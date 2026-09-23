from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import EnquiryViewSet, LeadWebhookView, TreatmentEstimateViewSet

router = DefaultRouter()
router.register("enquiries", EnquiryViewSet, basename="enquiry")
router.register("treatment-estimates", TreatmentEstimateViewSet, basename="treatment-estimate")

urlpatterns = [
    path("enquiries/lead-webhook/<uuid:token>/", LeadWebhookView.as_view(), name="enquiry-lead-webhook"),
] + router.urls

