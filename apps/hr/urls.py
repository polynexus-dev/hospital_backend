from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet, EmployeeViewSet, LeaveRequestViewSet, ShiftViewSet

router = DefaultRouter()
router.register(r"employees", EmployeeViewSet, basename="employee")
router.register(r"attendance", AttendanceViewSet, basename="attendance")
router.register(r"leave-requests", LeaveRequestViewSet, basename="leave-request")
router.register(r"shifts", ShiftViewSet, basename="shift")

from . import hrm_views  # noqa: E402

router.register(r"staff-profiles", hrm_views.StaffProfileViewSet, basename="staffprofile")
router.register(r"duty-rules", hrm_views.DutyRuleViewSet, basename="dutyrule")
router.register(r"roster-publications", hrm_views.RosterPublicationViewSet, basename="rosterpublication")
router.register(r"salary-structures", hrm_views.SalaryStructureViewSet, basename="salarystructure")
router.register(r"payroll-runs", hrm_views.PayrollRunViewSet, basename="payrollrun")
router.register(r"appraisals", hrm_views.AppraisalViewSet, basename="appraisal")
router.register(r"job-openings", hrm_views.JobOpeningViewSet, basename="jobopening")
router.register(r"candidates", hrm_views.CandidateViewSet, basename="candidate")
router.register(r"exits", hrm_views.ExitRequestViewSet, basename="exitrequest")
router.register(r"trainings", hrm_views.TrainingProgramViewSet, basename="trainingprogram")
urlpatterns = router.urls
