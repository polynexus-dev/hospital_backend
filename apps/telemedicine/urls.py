from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import PatientJoinView, TeleConsultationViewSet

router = DefaultRouter()
router.register("consultations", TeleConsultationViewSet, basename="teleconsultation")

urlpatterns = router.urls + [
    path("join/<str:token>/", PatientJoinView.as_view(), name="tele-patient-join"),
]
