from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.core.viewsets import TenantScopedViewSetMixin

from .models import CampRegistration, Campaign, CorporateClient, HealthPackage
from .serializers import (
    CampRegistrationSerializer,
    CampaignSerializer,
    CorporateClientSerializer,
    HealthPackageSerializer,
)


class HealthPackageViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = HealthPackageSerializer
    queryset = HealthPackage.objects.all()
    filterset_fields = ["category", "is_active"]
    search_fields = ["name", "code"]


class CampaignViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CampaignSerializer
    queryset = Campaign.objects.all()
    filterset_fields = ["campaign_type", "status"]
    search_fields = ["name"]

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.annotate(
            total_registrations=Count("registrations"),
            total_conversions=Count(
                "registrations",
                filter=Q(registrations__stage__in=[
                    CampRegistration.FunnelStage.OPD_CONVERTED,
                    CampRegistration.FunnelStage.IPD_CONVERTED,
                ]),
            ),
            total_revenue_generated=Sum("registrations__revenue_generated"),
        )

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        """GET ?days=90 — what the camp brought in: OPD registrations tagged
        with it (any payer), their bills, and the same patients' later bills
        within `days` (follow-up OPD and admissions)."""
        from apps.appointments.models import OPDVisit
        from apps.billing.models import Bill

        camp = self.get_object()
        try:
            days = min(max(int(request.query_params.get("days", 90)), 1), 730)
        except ValueError:
            days = 90
        visits = list(OPDVisit.objects.filter(campaign=camp).select_related("patient", "doctor", "bill").order_by("created_at"))
        first_seen, by_date, by_doctor, visit_bill_ids = {}, {}, {}, set()
        for v in visits:
            first_seen.setdefault(v.patient_id, v.created_at)
            day = v.created_at.date().isoformat()
            by_date[day] = by_date.get(day, 0) + 1
            name = v.doctor.name if v.doctor_id else "—"
            by_doctor[name] = by_doctor.get(name, 0) + 1
            if v.bill_id:
                visit_bill_ids.add(v.bill_id)
        live = Bill.objects.filter(is_interim=False).exclude(status__in=["cancelled", "draft"])
        camp_bills = live.filter(pk__in=visit_bill_ids)
        camp_revenue = camp_bills.aggregate(t=Sum("net_amount"))["t"] or Decimal("0")
        follow = {"opd": Decimal("0"), "ipd": Decimal("0"), "bills": 0, "patients": set(), "admissions": 0}
        for pid, seen in first_seen.items():
            for b in live.filter(patient_id=pid, created_at__gt=seen, created_at__lte=seen + timedelta(days=days)).exclude(pk__in=visit_bill_ids):
                follow["ipd" if b.admission_id else "opd"] += b.net_amount
                follow["bills"] += 1
                follow["patients"].add(pid)
                follow["admissions"] += 1 if b.admission_id else 0
        total = camp_revenue + follow["opd"] + follow["ipd"]
        spend = camp.actual_spend or Decimal("0")
        return Response({
            "camp": camp.name, "start_date": camp.start_date, "end_date": camp.end_date, "days": days,
            "registrations": len(visits), "patients": len(first_seen),
            "by_date": [{"date": d, "registrations": n} for d, n in sorted(by_date.items())],
            "by_doctor": [{"doctor": d, "registrations": n} for d, n in sorted(by_doctor.items(), key=lambda kv: -kv[1])],
            "camp_bills": camp_bills.count(), "camp_revenue": camp_revenue,
            "follow_up_patients": len(follow["patients"]), "follow_up_bills": follow["bills"], "follow_up_admissions": follow["admissions"],
            "follow_up_opd_revenue": follow["opd"], "follow_up_ipd_revenue": follow["ipd"],
            "total_revenue": total, "spend": spend, "return_on_spend": round(float(total / spend), 2) if spend else None,
        })


class CampRegistrationViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CampRegistrationSerializer
    # select_related: CampRegistrationSerializer.campaign_name (source="campaign.name")
    queryset = CampRegistration.objects.select_related("campaign")
    filterset_fields = ["campaign", "stage", "enquiry", "patient"]
    search_fields = ["patient_name", "mobile"]


class CorporateClientViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = CorporateClientSerializer
    queryset = CorporateClient.objects.all()
    filterset_fields = ["campaign", "billing_model", "is_active"]
    search_fields = ["name", "contact_person"]
