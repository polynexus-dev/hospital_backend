from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("cases", views.CancerCaseViewSet, basename="cancercase")
router.register("surgeries", views.SurgicalOncologyViewSet, basename="surgicaloncology")
router.register("chemo-protocols", views.ChemoProtocolViewSet, basename="chemoprotocol")
router.register("chemo-cycles", views.ChemoCycleViewSet, basename="chemocycle")
router.register("radiotherapy", views.RadiotherapyPlanViewSet, basename="radiotherapyplan")
router.register("tumor-boards", views.TumorBoardMeetingViewSet, basename="tumorboard")
router.register("trials", views.ClinicalTrialViewSet, basename="clinicaltrial")
router.register("trial-enrollments", views.TrialEnrollmentViewSet, basename="trialenrollment")
router.register("bmt", views.BoneMarrowTransplantViewSet, basename="bonemarrowtransplant")

urlpatterns = router.urls
