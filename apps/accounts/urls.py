from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from . import sso_views
from .views import ExpiredPasswordChangeView, HospitalTokenObtainPairView, MFAVerifyView, RoleViewSet, UserViewSet

router = DefaultRouter()
router.register("users", UserViewSet, basename="user")
router.register("roles", RoleViewSet, basename="role")
router.register("sso-providers", sso_views.SSOProviderViewSet, basename="ssoprovider")

urlpatterns = [
    path("auth/login/", HospitalTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # Blacklists the refresh token (requires rest_framework_simplejwt.token_blacklist
    # in INSTALLED_APPS + BLACKLIST_AFTER_ROTATION — see SIMPLE_JWT in settings)
    # so a token that's already been logged out of can't be replayed.
    path("auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    path("auth/mfa/verify/", MFAVerifyView.as_view(), name="mfa_verify"),
    path("auth/sso/providers/", sso_views.SSOProvidersView.as_view(), name="sso_providers"),
    path("auth/sso/<int:pk>/start/", sso_views.SSOStartView.as_view(), name="sso_start"),
    path("auth/sso/callback/", sso_views.SSOCallbackView.as_view(), name="sso_callback"),
    path("auth/sso/exchange/", sso_views.SSOExchangeView.as_view(), name="sso_exchange"),
    path("auth/password/expired-change/", ExpiredPasswordChangeView.as_view(), name="expired_password_change"),
    path("", include(router.urls)),
]
