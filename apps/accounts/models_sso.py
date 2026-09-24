"""Single sign-on (NABH DAC.1.e) — OpenID Connect with Microsoft Entra ID,
Google Workspace or any standards-compliant OIDC identity provider."""
from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedTextField
from apps.core.models import TenantScopedModel

MICROSOFT_DISCOVERY = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"


class SSOProvider(TenantScopedModel):
    class Kind(models.TextChoices):
        MICROSOFT = "microsoft", "Microsoft Entra ID (Microsoft 365)"
        GOOGLE = "google", "Google Workspace"
        OIDC = "oidc", "Other OpenID Connect provider"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    display_name = models.CharField(max_length=80, help_text='Shown on the login button, e.g. "Microsoft"')
    client_id = models.CharField(max_length=200)
    client_secret = EncryptedTextField()
    tenant_id = models.CharField(max_length=64, blank=True, help_text="Microsoft: your directory (tenant) ID — required")
    discovery_url = models.URLField(blank=True, help_text="Other OIDC providers: the .well-known/openid-configuration URL")
    allowed_domains = models.JSONField(default=list, blank=True, help_text='Only emails at these domains may sign in, e.g. ["cityhospital.in"]')
    is_enabled = models.BooleanField(default=True)

    class Meta:
        app_label = "accounts"
        ordering = ["display_name"]

    def __str__(self):
        return self.display_name

    @property
    def discovery(self):
        if self.kind == self.Kind.MICROSOFT:
            return MICROSOFT_DISCOVERY.format(tenant=self.tenant_id)
        if self.kind == self.Kind.GOOGLE:
            return GOOGLE_DISCOVERY
        return self.discovery_url


class SSOIdentity(models.Model):
    """Links a staff account to one identity at a provider (issuer + subject).
    Once linked, sign-in matches on this — not on the email — so a recycled
    email address at the provider can't take over the account."""

    provider = models.ForeignKey(SSOProvider, on_delete=models.CASCADE, related_name="identities")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sso_identities")
    subject = models.CharField(max_length=255)
    email_at_link = models.EmailField()
    linked_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "accounts"
        constraints = [
            models.UniqueConstraint(fields=["provider", "subject"], name="unique_sso_subject_per_provider"),
            models.UniqueConstraint(fields=["provider", "user"], name="one_sso_identity_per_user_per_provider"),
        ]


class SSOLoginAttempt(models.Model):
    """Server-side state for one sign-in round trip (state, nonce, PKCE
    verifier), then the one-time code the browser swaps for tokens."""

    provider = models.ForeignKey(SSOProvider, on_delete=models.CASCADE, related_name="+")
    state = models.CharField(max_length=64, unique=True)
    nonce = models.CharField(max_length=64)
    code_verifier = models.CharField(max_length=128)
    return_to = models.URLField()
    next_path = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    exchange_code_hash = models.CharField(max_length=64, blank=True, db_index=True)
    exchanged_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=60, blank=True)

    class Meta:
        app_label = "accounts"
        indexes = [models.Index(fields=["created_at"])]
