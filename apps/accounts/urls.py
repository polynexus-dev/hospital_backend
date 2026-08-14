from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .views import HospitalTokenObtainPairView, RoleViewSet, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")

urlpatterns = [
    path("auth/login/", HospitalTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # Blacklists the given refresh token so a stolen/leaked one can actually
    # be revoked before its natural expiry, not just left to time out.
    # No IsAuthenticated gate — mirrors TokenRefreshView (unauthenticated by
    # design, since the access token may already be gone by the time this
    # is called) and simplejwt's own TokenBlacklistView default.
    path("auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("", include(router.urls)),
]
