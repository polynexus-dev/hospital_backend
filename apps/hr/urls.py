from rest_framework.routers import DefaultRouter
from .views import AttendanceViewSet, EmployeeViewSet, LeaveRequestViewSet, ShiftViewSet

router = DefaultRouter()
router.register(r"employees", EmployeeViewSet, basename="employee")
router.register(r"attendance", AttendanceViewSet, basename="attendance")
router.register(r"leave-requests", LeaveRequestViewSet, basename="leave-request")
router.register(r"shifts", ShiftViewSet, basename="shift")

urlpatterns = router.urls
