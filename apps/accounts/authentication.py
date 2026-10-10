from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class ActiveUserJWTAuthentication(JWTAuthentication):
    """JWTAuthentication that re-checks the account on every request, so
    blocking someone (or deactivating them) ends their access immediately —
    not when their access token expires, nor when their refresh token runs out
    hours later. Inactive accounts are already refused by simplejwt."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if getattr(user, "is_blocked", False):
            raise AuthenticationFailed("This account has been blocked.", code="account_blocked")
        return user


def end_all_sessions(user) -> int:
    """Blacklist every refresh token the user holds, so no session can be
    renewed. (Access tokens are refused by ActiveUserJWTAuthentication.)"""
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

    ended = 0
    for token in OutstandingToken.objects.filter(user=user):
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        ended += created
    return ended
