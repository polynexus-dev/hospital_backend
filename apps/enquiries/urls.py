from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import EnquiryViewSet, LeadWebhookView, TreatmentEstimateViewSet
from .website import SiteConfigView, SiteEnquiryView, WebsiteWidgetAdminView, WidgetPreviewView, WidgetScriptView

router = DefaultRouter()
router.register("enquiries", EnquiryViewSet, basename="enquiry")
router.register("treatment-estimates", TreatmentEstimateViewSet, basename="treatment-estimate")

urlpatterns = [
    path("enquiries/lead-webhook/<uuid:token>/", LeadWebhookView.as_view(), name="enquiry-lead-webhook"),
    path("enquiries/website-widget/", WebsiteWidgetAdminView.as_view(), name="website-widget"),
    path("site/<str:site_key>/config/", SiteConfigView.as_view(), name="site-config"),
    path("site/<str:site_key>/enquiry/", SiteEnquiryView.as_view(), name="site-enquiry"),
    path("site/<str:site_key>/widget.js", WidgetScriptView.as_view(), name="site-widget-js"),
    path("site/<str:site_key>/preview/", WidgetPreviewView.as_view(), name="site-widget-preview"),
] + router.urls

