from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

class BasePatientAPIView(APIView):
    permission_classes = [IsAuthenticated]
    # In a full implementation, you'd restrict querysets to the logged-in user's linked Patient object(s).

from rest_framework import serializers
from django.shortcuts import get_object_or_404

from apps.patients.models import Patient
from apps.patients.serializers import PatientSerializer
from apps.core.models import Hospital, Department
from apps.core.views import DepartmentSerializer
from apps.appointments.models import Doctor
from apps.appointments.serializers import DoctorSerializer
from apps.appointments.views import doctor_queue, _doctor_schedule

class HospitalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hospital
        fields = ["id", "name", "slug"]

# Profile & Family
class ProfileView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id") if hasattr(request, "auth") else None
        if not patient_id:
            return Response({"error": "No patient associated with token"}, status=403)
        patient = get_object_or_404(Patient, pk=patient_id)
        return Response(PatientSerializer(patient).data)

    def patch(self, request):
        patient_id = request.auth.get("patient_id") if hasattr(request, "auth") else None
        if not patient_id:
            return Response({"error": "No patient associated with token"}, status=403)
        patient = get_object_or_404(Patient, pk=patient_id)
        serializer = PatientSerializer(patient, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

class FamilyView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        family = Patient.objects.filter(guardian_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(PatientSerializer(family, many=True).data)
        
    def post(self, request):
        patient_id = request.auth.get("patient_id")
        
        # Check if family member already exists
        first_name = request.data.get("first_name", "").strip()
        last_name = request.data.get("last_name", "").strip()
        
        if first_name and Patient.objects.filter(
            guardian_id=patient_id, 
            hospital_id=request.user.hospital_id,
            first_name__iexact=first_name,
            last_name__iexact=last_name
        ).exists():
            return Response(
                {"error": "A family member with this name already exists in your profile."}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
        serializer = PatientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(guardian_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class FamilyDetailView(BasePatientAPIView):
    def patch(self, request, pk):
        patient_id = request.auth.get("patient_id")
        member = get_object_or_404(Patient, pk=pk, guardian_id=patient_id, hospital_id=request.user.hospital_id)
        serializer = PatientSerializer(member, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
        
    def delete(self, request, pk):
        patient_id = request.auth.get("patient_id")
        member = get_object_or_404(Patient, pk=pk, guardian_id=patient_id, hospital_id=request.user.hospital_id)
        member.is_active = False
        member.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

class PreferencesView(BasePatientAPIView):
    def patch(self, request):
        patient_id = request.auth.get("patient_id")
        patient = get_object_or_404(Patient, pk=patient_id)
        
        # We only allow updating preference fields here
        allowed_fields = ["preferred_language", "payment_preference"]
        data = {k: v for k, v in request.data.items() if k in allowed_fields}
        
        serializer = PatientSerializer(patient, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# Hospitals & Doctors
class HospitalListView(BasePatientAPIView):
    def get(self, request):
        hospitals = Hospital.objects.all()
        return Response(HospitalSerializer(hospitals, many=True).data)

class HospitalDetailView(BasePatientAPIView):
    def get(self, request, pk):
        hospital = get_object_or_404(Hospital, pk=pk)
        return Response(HospitalSerializer(hospital).data)

class DepartmentListView(BasePatientAPIView):
    def get(self, request):
        departments = Department.objects.filter(hospital_id=request.user.hospital_id)
        return Response(DepartmentSerializer(departments, many=True).data)

class DoctorListView(BasePatientAPIView):
    def get(self, request):
        qs = Doctor.objects.filter(hospital_id=request.user.hospital_id)
        if "department" in request.query_params:
            qs = qs.filter(department_id=request.query_params["department"])
        if "q" in request.query_params:
            qs = qs.filter(name__icontains=request.query_params["q"])
        return Response(DoctorSerializer(qs, many=True).data)

class DoctorDetailView(BasePatientAPIView):
    def get(self, request, pk):
        doctor = get_object_or_404(Doctor, pk=pk, hospital_id=request.user.hospital_id)
        return Response(DoctorSerializer(doctor).data)

class DoctorSlotsView(BasePatientAPIView):
    def get(self, request, pk):
        from django.utils.dateparse import parse_date
        doctor = get_object_or_404(Doctor, pk=pk, hospital_id=request.user.hospital_id)
        day_str = request.query_params.get("date")
        day = parse_date(day_str) if day_str else None
        if not day:
            return Response({"error": "date query param is required in YYYY-MM-DD format"}, status=400)
        
        # Query the slots directly to ensure we get the slot ID needed for booking
        slots = Slot.objects.filter(doctor=doctor, date=day).order_by("start_time")
        
        out = []
        for slot in slots:
            # If a slot is blocked, or if an appointment is already linked to it, it is not "free"
            status_text = "free"
            if slot.is_blocked:
                status_text = "blocked"
            elif hasattr(slot, "appointment") and slot.appointment.status not in ["cancelled", "no_show", "rescheduled"]:
                status_text = "booked"
                
            out.append({
                "id": slot.id,
                "time": slot.start_time.strftime("%H:%M"),
                "status": status_text
            })
            
        return Response(out)


from apps.appointments.models import Appointment, Slot
from apps.appointments.serializers import (
    AppointmentSerializer, 
    BookAppointmentSerializer, 
    RescheduleAppointmentSerializer
)
from apps.appointments.services import (
    book_appointment, 
    cancel, 
    reschedule_appointment, 
    check_in, 
    SlotUnavailable
)
from rest_framework import status

# Appointments
class AppointmentListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        qs = Appointment.objects.filter(hospital_id=request.user.hospital_id, patient_id=patient_id).order_by("-created_at")
        if "status" in request.query_params:
            if request.query_params["status"] == "upcoming":
                qs = qs.exclude(status__in=["cancelled", "completed", "no_show"])
            elif request.query_params["status"] == "past":
                qs = qs.filter(status__in=["completed", "no_show", "cancelled"])
        return Response(AppointmentSerializer(qs, many=True).data)

    def post(self, request):
        logged_in_patient_id = request.auth.get("patient_id")
        
        serializer = BookAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        
        # Determine who the appointment is for (main profile or family member)
        target_patient_id = data.get("patient")
        
        if target_patient_id == logged_in_patient_id:
            patient = get_object_or_404(Patient, pk=target_patient_id, hospital_id=request.user.hospital_id)
        else:
            # If booking for someone else, ensure they are a registered family member
            patient = get_object_or_404(Patient, pk=target_patient_id, guardian_id=logged_in_patient_id, hospital_id=request.user.hospital_id)
        
        slot = get_object_or_404(Slot, pk=data["slot"])
        try:
            appointment = book_appointment(
                patient=patient, slot=slot, source="app", reason=data.get("reason", ""), booked_by=request.user
            )
        except SlotUnavailable as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
            
        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)

class AppointmentDetailView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        appointment = get_object_or_404(Appointment, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(AppointmentSerializer(appointment).data)

class AppointmentRescheduleView(BasePatientAPIView):
    def post(self, request, pk):
        patient_id = request.auth.get("patient_id")
        appointment = get_object_or_404(Appointment, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        
        serializer = RescheduleAppointmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_slot = get_object_or_404(Slot, pk=serializer.validated_data["new_slot"])
        
        try:
            new_appointment = reschedule_appointment(appointment, new_slot=new_slot, changed_by=request.user)
        except SlotUnavailable as exc:
            return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
            
        return Response(AppointmentSerializer(new_appointment).data, status=status.HTTP_201_CREATED)

class AppointmentCancelView(BasePatientAPIView):
    def post(self, request, pk):
        patient_id = request.auth.get("patient_id")
        appointment = get_object_or_404(Appointment, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        cancelled_apt = cancel(appointment)
        return Response(AppointmentSerializer(cancelled_apt).data)

class AppointmentQueueView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        appointment = get_object_or_404(Appointment, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response({
            "queue_status": appointment.status, 
            "token": appointment.queue_token or "N/A"
        })

class AppointmentCheckinView(BasePatientAPIView):
    def post(self, request, pk):
        patient_id = request.auth.get("patient_id")
        appointment = get_object_or_404(Appointment, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        
        if appointment.status in ["cancelled", "rescheduled", "no_show", "completed"]:
            return Response(
                {"error": f"Cannot check-in to an appointment that is {appointment.status}"},
                status=400
            )
            
        checked_in_apt = check_in(appointment)
        return Response(AppointmentSerializer(checked_in_apt).data)


# Payments & Billing
from apps.billing.models import Bill
from apps.billing.serializers import BillSerializer

class PaymentInitiateView(BasePatientAPIView):
    def post(self, request): return Response({"order_id": "ORDER_123"}) # Kept as mock (gateway specific)

class PaymentVerifyView(BasePatientAPIView):
    def post(self, request): return Response({"status": "Verified"})

class PaymentWebhookView(APIView):
    def post(self, request): return Response({"message": "Received"})

class BillListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        bills = Bill.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(BillSerializer(bills, many=True).data)

class BillDetailView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        bill = get_object_or_404(Bill, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(BillSerializer(bill).data)

class BillReceiptView(BasePatientAPIView):
    def get(self, request, pk):
        # In a real system this generates/returns a PDF URL
        return Response({"pdf_url": f"https://api.hospital.demo/media/receipts/{pk}.pdf"})


# Medical Records
from apps.patients.models import Prescription, Document
from apps.patients.serializers import PrescriptionSerializer, DocumentSerializer

class PrescriptionListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        qs = Prescription.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id).order_by("-created_at")
        return Response(PrescriptionSerializer(qs, many=True).data)

class PrescriptionDetailView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        prescription = get_object_or_404(Prescription, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(PrescriptionSerializer(prescription).data)

class ReportListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        qs = Document.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id, category="lab_report").order_by("-created_at")
        return Response(DocumentSerializer(qs, many=True).data)

class ReportFileView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        doc = get_object_or_404(Document, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response({"file_url": doc.file.url if doc.file else None})

class VisitListView(BasePatientAPIView):
    def get(self, request):
        # Return completed appointments as visits
        patient_id = request.auth.get("patient_id")
        qs = Appointment.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id, status="completed").order_by("-created_at")
        return Response(AppointmentSerializer(qs, many=True).data)

class DocumentListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        qs = Document.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id).order_by("-created_at")
        return Response(DocumentSerializer(qs, many=True).data)
        
    def post(self, request):
        patient_id = request.auth.get("patient_id")
        serializer = DocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(patient_id=patient_id, hospital_id=request.user.hospital_id, uploaded_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# Packages & Camps
from apps.packages.models import HealthPackage, Campaign, CampRegistration
from apps.packages.serializers import HealthPackageSerializer, CampaignSerializer

class PackageListView(BasePatientAPIView):
    def get(self, request):
        qs = HealthPackage.objects.filter(hospital_id=request.user.hospital_id, is_active=True)
        return Response(HealthPackageSerializer(qs, many=True).data)

class PackageDetailView(BasePatientAPIView):
    def get(self, request, pk):
        pkg = get_object_or_404(HealthPackage, pk=pk, hospital_id=request.user.hospital_id)
        return Response(HealthPackageSerializer(pkg).data)

class PackageBookView(BasePatientAPIView):
    def post(self, request, pk):
        return Response({"message": "Package booked via mobile API"})

class CampListView(BasePatientAPIView):
    def get(self, request):
        qs = Campaign.objects.filter(
            hospital_id=request.user.hospital_id, 
            campaign_type=Campaign.CampaignType.HEALTH_CAMP, 
            status=Campaign.Status.ACTIVE
        )
        return Response(CampaignSerializer(qs, many=True).data)

class CampRegisterView(BasePatientAPIView):
    def post(self, request, pk):
        patient_id = request.auth.get("patient_id")
        patient = get_object_or_404(Patient, pk=patient_id)
        camp = get_object_or_404(Campaign, pk=pk, hospital_id=request.user.hospital_id, campaign_type=Campaign.CampaignType.HEALTH_CAMP)
        
        # Check if already registered
        if CampRegistration.objects.filter(campaign=camp, patient=patient).exists():
            return Response({"error": "You are already registered for this camp."}, status=400)
            
        CampRegistration.objects.create(
            hospital=request.user.hospital,
            campaign=camp,
            patient=patient,
            patient_name=patient.full_name,
            mobile=patient.mobile
        )
        
        return Response({"message": "Successfully registered for the camp!"})


# Teleconsult
class TeleconsultSessionView(BasePatientAPIView):
    def post(self, request): return Response({"session_id": "SESS_123"})

class TeleconsultTokenView(BasePatientAPIView):
    def get(self, request, pk): return Response({"token": "VIDEO_TOKEN_ABC"})


# Support & Engagement
from apps.communications.models import Message
from apps.communications.serializers import MessageSerializer

class CallbackRequestView(BasePatientAPIView):
    def post(self, request):
        from apps.telephony.models import CallbackTask
        patient_id = request.auth.get("patient_id")
        patient = get_object_or_404(Patient, pk=patient_id)
        
        task = CallbackTask.objects.create(
            hospital=request.user.hospital,
            patient=patient,
            mobile=patient.mobile,
            source="patient_app",
            status=CallbackTask.Status.PENDING,
        )
        return Response({"message": "Callback requested successfully!", "task_id": task.id})

class NotificationListView(BasePatientAPIView):
    def get(self, request):
        patient_id = request.auth.get("patient_id")
        qs = Message.objects.filter(patient_id=patient_id, hospital_id=request.user.hospital_id).order_by("-created_at")
        return Response(MessageSerializer(qs, many=True).data)

class NotificationReadView(BasePatientAPIView):
    def patch(self, request, pk):
        patient_id = request.auth.get("patient_id")
        msg = get_object_or_404(Message, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        msg.status = "read"
        msg.save(update_fields=["status"])
        return Response(MessageSerializer(msg).data)

class FeedbackView(BasePatientAPIView):
    def post(self, request):
        from apps.feedback.models import Complaint
        patient_id = request.auth.get("patient_id")
        patient = get_object_or_404(Patient, pk=patient_id)
        
        description = request.data.get("description", "General feedback")
        complaint = Complaint.objects.create(
            hospital=request.user.hospital,
            patient=patient,
            description=description,
            status=Complaint.Status.OPEN
        )
        return Response({"message": "Feedback submitted successfully!", "complaint_id": complaint.id})

class AssistantMessageView(BasePatientAPIView):
    def post(self, request): 
        # This remains a mock since we don't have a full AI integration in the backend yet.
        return Response({"reply": "I am your AI assistant! How can I help you today?"})


# Insurance
from apps.tpa.models import PreAuthRequest
from apps.tpa.serializers import PreAuthRequestSerializer

class InsuranceView(BasePatientAPIView):
    def get(self, request): return Response([])
    def post(self, request): return Response({"message": "Insurance added"})

class PreauthStatusView(BasePatientAPIView):
    def get(self, request, pk):
        patient_id = request.auth.get("patient_id")
        preauth = get_object_or_404(PreAuthRequest, pk=pk, patient_id=patient_id, hospital_id=request.user.hospital_id)
        return Response(PreAuthRequestSerializer(preauth).data)
