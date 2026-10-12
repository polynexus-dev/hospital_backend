from rest_framework.routers import DefaultRouter

from .views import ConsentNoticeViewSet, DataRightsRequestViewSet, GrievanceTicketViewSet, NomineeViewSet, PatientConsentViewSet

router = DefaultRouter()
router.register(r"data-rights-requests", DataRightsRequestViewSet, basename="datarightsrequest")
router.register(r"grievances", GrievanceTicketViewSet, basename="grievanceticket")
router.register(r"nominees", NomineeViewSet, basename="nominee")
router.register(r"consent-notices", ConsentNoticeViewSet, basename="consentnotice")
router.register(r"consents", PatientConsentViewSet, basename="patientconsent")

urlpatterns = router.urls
