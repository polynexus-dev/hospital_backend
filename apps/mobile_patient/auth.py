from django.contrib.auth import get_user_model
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from apps.core.models import Hospital
from apps.patients.models import Patient
from apps.patients.registration import issue_otp, verify_otp
from apps.core.encryption import blind_index, normalize_phone

User = get_user_model()


class PatientJWTAuthentication(JWTAuthentication):
    """Authenticates ONLY patient-app tokens — those minted by VerifyOTPView,
    which carry a `patient_id` claim. A staff token (no such claim) is refused
    here, and these patient tokens are refused by the staff API
    (apps.accounts.authentication.ActiveUserJWTAuthentication), so an OTP
    login can never be spent on staff endpoints."""

    def get_validated_token(self, raw_token):
        token = super().get_validated_token(raw_token)
        if not token.get("patient_id"):
            raise InvalidToken("This token is not valid for the patient app.")
        return token

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if getattr(user, "is_blocked", False):
            raise AuthenticationFailed("This account has been blocked.", code="account_blocked")
        return user


def _resolve_hospital(request):
    """The hospital an OTP request is for. Prefers an explicit `hospital` slug,
    then the tenant resolved from the subdomain (TenantMiddleware); falls back
    to the single hospital only on a one-hospital (on-premise) install. Never
    silently binds to an arbitrary `Hospital.objects.first()` on a multi-tenant
    deployment."""
    slug = str(request.data.get("hospital") or "").strip().lower()
    if slug:
        return Hospital.objects.filter(slug=slug, is_active=True).first()
    tenant = getattr(request, "tenant", None)
    if tenant is not None:
        return tenant
    actives = list(Hospital.objects.filter(is_active=True)[:2])
    return actives[0] if len(actives) == 1 else None


class SendOTPView(APIView):
    permission_classes = [AllowAny]
    # Same brute-force concern as the staff login: no auth gates it, and each
    # call sends an SMS, so bound it per IP.
    throttle_scope = "login"

    def post(self, request):
        mobile = request.data.get("mobile")
        if not mobile:
            return Response({"error": "Mobile number is required"}, status=400)

        hospital = _resolve_hospital(request)
        if hospital is None:
            return Response({"error": "Unknown hospital. Pass a valid 'hospital' slug."}, status=400)
        # The OTP is delivered by SMS only (issue_otp) — never returned in the
        # response, which anyone who knows a phone number could otherwise read
        # to take over that patient's account.
        issue_otp(hospital, mobile)
        return Response({"message": "OTP sent successfully"})


class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        mobile = request.data.get("mobile")
        otp = request.data.get("otp")
        if not mobile or not otp:
            return Response({"error": "Mobile and OTP are required"}, status=400)

        hospital = _resolve_hospital(request)
        if hospital is None:
            return Response({"error": "Unknown hospital. Pass a valid 'hospital' slug."}, status=400)
        ok, err = verify_otp(hospital, mobile, otp)
        if not ok:
            return Response({"error": err}, status=400)

        # Look for existing patient by mobile
        normalized_mobile = normalize_phone(mobile)
        patient = Patient.objects.filter(
            hospital=hospital,
            mobile_hash=blind_index(normalized_mobile)
        ).first()

        is_new_user = False
        if not patient:
            patient = Patient.objects.create(hospital=hospital, mobile=mobile, first_name="")
            is_new_user = True

        # Generate a User account for JWT token issuing
        email = f"{normalized_mobile.replace('+', '')}@patient.local"
        user, _ = User.objects.get_or_create(email=email, defaults={
            "first_name": patient.first_name or "Patient",
            "hospital": hospital,
            "is_active": True
        })

        refresh = RefreshToken.for_user(user)
        refresh["patient_id"] = patient.pk

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "patient_id": patient.pk,
            "is_new_user": is_new_user
        })

class TokenRefreshView(SimpleJWTTokenRefreshView):
    pass

class LogoutView(APIView):
    authentication_classes = [PatientJWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request):
        return Response({"message": "Logged out successfully (Clear your token locally)"})

class DeviceRegistrationView(APIView):
    authentication_classes = [PatientJWTAuthentication]
    permission_classes = [IsAuthenticated]
    def post(self, request):
        fcm_token = request.data.get("fcm_token")
        # In a real app, save this token to a Device/FCM model
        return Response({"message": "Device FCM token registered"})
