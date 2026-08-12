from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AIChatbotView,
    ConsentOptOutViewSet,
    InboundWebhookView,
    MessageViewSet,
    TemplateViewSet,
    ThreadViewSet,
)

router = DefaultRouter()
router.register("templates", TemplateViewSet, basename="template")
router.register("consent", ConsentOptOutViewSet, basename="consentoptout")
router.register("messages", MessageViewSet, basename="message")
router.register("threads", ThreadViewSet, basename="thread")

urlpatterns = [
    path("messages/ai-chat/", AIChatbotView.as_view(), name="ai-chat"),
    path("webhooks/inbound/<uuid:hospital_id>/<str:channel>/", InboundWebhookView.as_view(), name="inbound-webhook"),
] + router.urls


