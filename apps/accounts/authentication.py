from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken


class ActiveUserJWTAuthentication(JWTAuthentication):
    """JWTAuthentication that re-checks the account on every request, so
    blocking someone (or deactivating them) ends their access immediately —
    not when their access token expires, nor when their refresh token runs out
    hours later. Inactive accounts are already refused by simplejwt.

    Patient-app tokens (which carry a `patient_id` claim, minted by
    apps.mobile_patient.auth.VerifyOTPView) are refused here: they are valid
    only on the patient mobile surface, never the staff API — otherwise an OTP
    login would authenticate as a baseline-permissioned hospital user."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        if token.get("patient_id"):
            raise InvalidToken("This token is not valid for the staff API.")
        return token

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
