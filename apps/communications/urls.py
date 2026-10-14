from django.urls import path
from rest_framework.routers import DefaultRouter

from .delivery import MSG91DeliveryWebhookView, UnsubscribeView
from .webchat import ChatMessagesView, ChatStartView
from .views import (
    AIChatbotView,
    BroadcastCampaignViewSet,
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
router.register("broadcasts", BroadcastCampaignViewSet, basename="broadcast")

urlpatterns = [
    path("site/<str:site_key>/chat/start/", ChatStartView.as_view(), name="site-chat-start"),
    path("site/<str:site_key>/chat/messages/", ChatMessagesView.as_view(), name="site-chat-messages"),
    path("messages/ai-chat/", AIChatbotView.as_view(), name="ai-chat"),
    path("webhooks/inbound/<uuid:hospital_id>/<str:channel>/", InboundWebhookView.as_view(), name="inbound-webhook"),
    path("webhooks/msg91/<str:token>/", MSG91DeliveryWebhookView.as_view(), name="msg91-delivery-webhook"),
    path("unsubscribe/<str:token>/", UnsubscribeView.as_view(), name="unsubscribe"),
] + router.urls


