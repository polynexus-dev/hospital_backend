from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("device-episodes", views.DeviceEpisodeViewSet, basename="deviceepisode")
router.register("hai", views.HAIIncidentViewSet, basename="haiincident")
router.register("antimicrobial-policies", views.AntimicrobialPolicyViewSet, basename="antimicrobialpolicy")
router.register("antimicrobial-approvals", views.AntimicrobialApprovalViewSet, basename="antimicrobialapproval")
router.register("staff-exposures", views.StaffExposureViewSet, basename="staffexposure")
router.register("hand-hygiene-audits", views.HandHygieneAuditViewSet, basename="handhygieneaudit")

urlpatterns = router.urls
