from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from .views import ExpiredPasswordChangeView, HospitalTokenObtainPairView, MFAVerifyView, RoleViewSet, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")

urlpatterns = [
    path("auth/login/", HospitalTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # Blacklists the refresh token (requires rest_framework_simplejwt.token_blacklist
    # in INSTALLED_APPS + BLACKLIST_AFTER_ROTATION — see SIMPLE_JWT in settings)
    # so a token that's already been logged out of can't be replayed.
    path("auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("auth/mfa/verify/", MFAVerifyView.as_view(), name="mfa_verify"),
    path("auth/password/expired-change/", ExpiredPasswordChangeView.as_view(), name="expired_password_change"),
    path("", include(router.urls)),
]
