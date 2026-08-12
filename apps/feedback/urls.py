from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ComplaintViewSet,
    FeedbackRequestViewSet,
    NPSResponseViewSet,
    ServiceRecoveryTaskViewSet,
    SubmitFeedbackView,
)

router = DefaultRouter()
router.register("feedback-requests", FeedbackRequestViewSet, basename="feedbackrequest")
router.register("nps-responses", NPSResponseViewSet, basename="npsresponse")
router.register("complaints", ComplaintViewSet, basename="complaint")
router.register("service-recovery-tasks", ServiceRecoveryTaskViewSet, basename="servicerecoverytask")

urlpatterns = router.urls + [
    path("feedback/<str:token>/", SubmitFeedbackView.as_view(), name="submit-feedback"),
]
