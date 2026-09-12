from decimal import Decimal
from django.db.models import Count, Sum
from django.http import HttpResponse
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.viewsets import TenantScopedViewSetMixin

from .models import FieldVisit, ReferralRecord, ReferringDoctor
from .referral_statement_pdf import render_referral_statement_pdf
from .serializers import (
    FieldVisitSerializer,
    ReferralRecordSerializer,
    ReferringDoctorSerializer,
)


class ReferringDoctorViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ReferringDoctorSerializer
    queryset = ReferringDoctor.objects.all()
    filterset_fields = ["tier", "is_active", "city"]
    search_fields = ["name", "clinic_name", "mobile", "speciality"]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            total_referrals=Count("referrals"),
            total_attributed_revenue=Sum("referrals__attributed_revenue"),
        )

    @action(detail=False, methods=["get"], url_path="league-table")
    def league_table(self, request):
        """Returns top referring doctors ranked by revenue attribution."""
        top_doctors = self.get_queryset().order_by("-total_attributed_revenue")[:10]
        return Response(ReferringDoctorSerializer(top_doctors, many=True).data)

    @action(detail=True, methods=["get"], url_path="statement-pdf")
    def statement_pdf(self, request, pk=None):
        """Generates itemized commission statement PDF for doctor liaison payout."""
        doctor = self.get_object()
        records = doctor.referrals.all().select_related("patient", "department")
        pdf_bytes = render_referral_statement_pdf(doctor, records)
        sanitized_name = "".join(c for c in doctor.name if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
        filename = f"Referral_Statement_{sanitized_name}_{doctor.pk}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{filename}"'
        return response

    @action(detail=True, methods=["post"], url_path="settle")
    def settle(self, request, pk=None):
        """Marks all converted pending referrals as paid for this doctor."""
        doctor = self.get_object()
        updated_count = doctor.referrals.filter(status=ReferralRecord.Status.CONVERTED).update(status=ReferralRecord.Status.PAID)
        return Response({"settled_count": updated_count, "detail": f"Settled {updated_count} referral cases for Dr. {doctor.name}."})



class ReferralRecordViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ReferralRecordSerializer
    queryset = ReferralRecord.objects.all()
    filterset_fields = ["referring_doctor", "patient", "status", "department"]


class FieldVisitViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = FieldVisitSerializer
    queryset = FieldVisit.objects.all()
    filterset_fields = ["referring_doctor", "visited_by"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, visited_by=self.request.user)
