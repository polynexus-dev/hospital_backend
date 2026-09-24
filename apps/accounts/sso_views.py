from urllib.parse import quote

from django.http import HttpResponseRedirect
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.crud import TenantCRUDViewSet, TenantModelSerializer
from apps.core.permissions import RequiresViewPermission

from . import sso
from .models_sso import SSOIdentity, SSOProvider


class _Public(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"


def _hospital_for(request):
    from apps.core.models import Hospital

    slug = (request.query_params.get("hospital") or "").strip().lower()
    if slug:
        return Hospital.objects.filter(slug=slug, is_active=True).first()
    return getattr(request, "tenant", None)


def password_login_allowed(hospital) -> bool:
    policy = getattr(hospital, "security_policy", None) if hospital else None
    return not (policy and policy.sso_required and SSOProvider.objects.filter(hospital=hospital, is_enabled=True).exists())


class SSOProvidersView(_Public):
    """GET ?hospital=<slug> (or the subdomain) — the sign-in buttons for the login page."""

    def get(self, request):
        hospital = _hospital_for(request)
        if hospital is None:
            return Response({"providers": [], "password_login": True})
        providers = SSOProvider.objects.filter(hospital=hospital, is_enabled=True)
        return Response({
            "providers": [{"id": p.pk, "name": p.display_name, "kind": p.kind} for p in providers],
            "password_login": password_login_allowed(hospital),
        })


class SSOStartView(_Public):
    """GET ?return_to=<frontend origin>&next=/path — redirects to the identity provider."""

    def get(self, request, pk):
        provider = SSOProvider.objects.filter(pk=pk, is_enabled=True).first()
        if provider is None:
            return Response({"detail": "Unknown sign-in provider."}, status=status.HTTP_404_NOT_FOUND)
        try:
            url = sso.start(provider, request, request.query_params.get("return_to", ""), request.query_params.get("next", ""))
        except sso.SSOError as e:
            return Response({"detail": e.detail, "code": e.code}, status=status.HTTP_400_BAD_REQUEST)
        return HttpResponseRedirect(url)


class SSOCallbackView(_Public):
    """The identity provider redirects here; we send the browser back to the login page."""

    def get(self, request):
        qp = request.query_params
        try:
            attempt, one_time = sso.complete(request, qp.get("state", ""), qp.get("code", ""), qp.get("error_description") or qp.get("error", ""))
        except sso.SSOError as e:
            attempt = getattr(e, "attempt", None)
            if attempt is None:
                return Response({"detail": e.detail, "code": e.code}, status=status.HTTP_400_BAD_REQUEST)
            return HttpResponseRedirect(f"{attempt.return_to}/login#sso_error={quote(e.code)}")
        return HttpResponseRedirect(f"{attempt.return_to}/login#sso={quote(one_time)}")


class SSOExchangeView(_Public):
    """POST {code} — the one-time code from the redirect, for the usual tokens."""

    def post(self, request):
        try:
            return Response(sso.exchange(request.data.get("code", "")))
        except sso.SSOError as e:
            return Response({"detail": e.detail, "code": e.code}, status=status.HTTP_400_BAD_REQUEST)


class SSOProviderSerializer(TenantModelSerializer):
    client_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_secret = serializers.SerializerMethodField()
    linked_users = serializers.IntegerField(source="identities.count", read_only=True)

    class Meta:
        model = SSOProvider
        fields = ["id", "kind", "display_name", "client_id", "client_secret", "has_secret", "tenant_id", "discovery_url",
                  "allowed_domains", "is_enabled", "linked_users", "created_at"]

    def get_has_secret(self, obj):
        return bool(obj.client_secret)

    def validate_allowed_domains(self, value):
        if not isinstance(value, list) or any(not isinstance(d, str) or "." not in d for d in value):
            raise serializers.ValidationError('A list of domains, e.g. ["cityhospital.in"].')
        return [d.strip().lower().lstrip("@") for d in value]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs.get("client_secret"):
            attrs.pop("client_secret", None)  # blank on edit = keep the stored secret
        kind = attrs.get("kind") or getattr(self.instance, "kind", None)
        if self.instance is None and not attrs.get("client_secret"):
            raise serializers.ValidationError({"client_secret": "Required."})
        if kind == SSOProvider.Kind.MICROSOFT and not (attrs.get("tenant_id") or getattr(self.instance, "tenant_id", "")):
            raise serializers.ValidationError({"tenant_id": "Microsoft needs your directory (tenant) ID — a GUID from Entra ID › Overview."})
        if kind == SSOProvider.Kind.OIDC and not (attrs.get("discovery_url") or getattr(self.instance, "discovery_url", "")):
            raise serializers.ValidationError({"discovery_url": "Required for other OpenID Connect providers."})
        return attrs


class SSOProviderViewSet(TenantCRUDViewSet):
    """Hospital SSO configuration (administrators)."""

    permission_classes = TenantCRUDViewSet.permission_classes + [RequiresViewPermission]
    serializer_class = SSOProviderSerializer
    queryset = SSOProvider.objects.all()
    audited_fields = ("kind", "client_id", "tenant_id", "allowed_domains", "is_enabled")
    action_permissions = {"test": "accounts.change_ssoprovider", "setup": "accounts.view_ssoprovider"}

    @action(detail=False, methods=["get"])
    def setup(self, request):
        """What to register at the identity provider."""
        return Response({"redirect_uri": sso.callback_url(request), "scopes": "openid email profile",
                         "password_login": password_login_allowed(request.user.hospital)})

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        """Checks the provider's discovery document is reachable and well-formed."""
        provider = self.get_object()
        sso._cache.pop(provider.discovery, None)
        try:
            meta = sso.discovery(provider)
        except sso.SSOError as e:
            return Response({"ok": False, "detail": e.detail}, status=status.HTTP_400_BAD_REQUEST)
        missing = [k for k in ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri") if not meta.get(k)]
        return Response({"ok": not missing, "issuer": meta.get("issuer"), "missing": missing})

    @action(detail=True, methods=["get"])
    def identities(self, request, pk=None):
        provider = self.get_object()
        return Response([{"id": i.pk, "user": i.user.email, "email_at_link": i.email_at_link, "linked_at": i.linked_at,
                          "last_login_at": i.last_login_at} for i in provider.identities.select_related("user")])

    @action(detail=True, methods=["post"], url_path="unlink")
    def unlink(self, request, pk=None):
        """POST {identity} — e.g. when a staff member's work account was recreated."""
        provider = self.get_object()
        deleted, _ = SSOIdentity.objects.filter(provider=provider, pk=request.data.get("identity")).delete()
        self._log("update", provider)
        return Response({"unlinked": deleted})
