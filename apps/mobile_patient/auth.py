import secrets
from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView as SimpleJWTTokenRefreshView

from apps.core.models import Hospital
from apps.patients.models import Patient
from apps.patients.registration import issue_otp, verify_otp
from apps.core.encryption import blind_index, normalize_phone

User = get_user_model()

class SendOTPView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        mobile = request.data.get("mobile")
        if not mobile:
            return Response({"error": "Mobile number is required"}, status=400)
            
        hospital = Hospital.objects.first() # Defaults to first hospital
        otp_code = issue_otp(hospital, mobile)
        return Response({"message": "OTP sent successfully", "otp": otp_code})

class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        mobile = request.data.get("mobile")
        otp = request.data.get("otp")
        if not mobile or not otp:
            return Response({"error": "Mobile and OTP are required"}, status=400)

        hospital = Hospital.objects.first()
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
    permission_classes = [IsAuthenticated]
    def post(self, request):
        return Response({"message": "Logged out successfully (Clear your token locally)"})

class DeviceRegistrationView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        fcm_token = request.data.get("fcm_token")
        # In a real app, save this token to a Device/FCM model
        return Response({"message": "Device FCM token registered"})
