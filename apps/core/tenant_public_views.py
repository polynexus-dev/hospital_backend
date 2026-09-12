from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ALL_MODULES, Hospital, RESERVED_HOSPITAL_SLUGS


class PublicTenantBrandingView(APIView):
    """
    Public unauthenticated endpoint to resolve hospital tenant branding
    based on incoming subdomain (or ?subdomain= query parameter in local dev).
    Used by the frontend to render dynamic hospital titles, themes, and badges.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        subdomain = request.GET.get("subdomain") or request.GET.get("hospital")
        if not subdomain:
            # Fallback: inspect host header
            host = request.get_host().split(":")[0].lower()
            parts = host.split(".")
            if len(parts) >= 3 and parts[0] not in {"www", "api", "hms", "crm", "admin"}:
                subdomain = parts[0]
            elif len(parts) == 2 and parts[1] == "localhost" and parts[0] not in {"www", "api"}:
                subdomain = parts[0]

        if not subdomain or subdomain.lower() in RESERVED_HOSPITAL_SLUGS or subdomain.lower() in {"hms", "crm", "hospital", "app"}:
            return Response({
                "is_tenant": False,
                "name": "Polynexus Healthcare OS",
                "subdomain": None,
                "city": None,
                "state": None,
                "enabled_modules": ALL_MODULES,
                "primary_language": "en",
                "theme": {
                    "primary_color": "#0284c7",
                    "accent_color": "#0f766e",
                },
            })

        subdomain = subdomain.strip().lower()
        hospital = Hospital.objects.filter(slug=subdomain).first()
        if not hospital:
            return Response(
                {"error": f"Hospital tenant '{subdomain}' does not exist on this platform."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "is_tenant": True,
            "id": str(hospital.id),
            "name": hospital.name,
            "slug": hospital.slug,
            "city": hospital.city,
            "state": hospital.state,
            "address": hospital.address,
            "primary_language": hospital.primary_language,
            "is_active": hospital.is_active,
            "enabled_modules": hospital.enabled_modules or ALL_MODULES,
            "google_review_url": hospital.google_review_url,
            "theme": {
                "primary_color": "#0f766e",
                "accent_color": "#1e3a8a",
            },
        })
