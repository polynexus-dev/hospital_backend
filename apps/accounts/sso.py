"""OpenID Connect sign-in (Authorization Code flow + PKCE).

    browser ── /auth/sso/<id>/start/ ──▶ identity provider (login, MFA)
    provider ── /auth/sso/callback/?code&state ──▶ we exchange the code,
               validate the ID token, find the staff account
    browser ◀── <frontend>/login#sso=<one-time code>
    browser ── POST /auth/sso/exchange/ ──▶ the usual access/refresh tokens

Tokens never travel in a URL; the one-time code is useless after one use or
two minutes. Accounts are never created here — an administrator creates the
user first, SSO only proves who is signing in.
"""
import base64
import hashlib
import logging
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import jwt
import requests
from django.conf import settings
from django.utils import timezone

from .models_sso import SSOIdentity, SSOLoginAttempt, SSOProvider

logger = logging.getLogger(__name__)
ATTEMPT_TTL = timedelta(minutes=10)
EXCHANGE_TTL = timedelta(minutes=2)
DISCOVERY_TTL = 3600
HTTP_TIMEOUT = 10
_cache: dict[str, tuple[float, dict]] = {}


class SSOError(Exception):
    """`code` is what the login page shows (mapped to a friendly message)."""

    def __init__(self, code, detail=""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


# ------------------------------------------------------------ HTTP (patched in tests)

def http_get_json(url):
    resp = requests.get(url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def http_post_form(url, data):
    resp = requests.post(url, data=data, timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"})
    if resp.status_code >= 400:
        raise SSOError("token_exchange_failed", f"The identity provider rejected the sign-in ({resp.status_code}).")
    return resp.json()


def _cached(url):
    hit = _cache.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    data = http_get_json(url)
    _cache[url] = (time.time() + DISCOVERY_TTL, data)
    return data


def discovery(provider):
    if not provider.discovery:
        raise SSOError("misconfigured", "The provider has no discovery URL / tenant ID.")
    try:
        return _cached(provider.discovery)
    except requests.RequestException as e:
        raise SSOError("provider_unreachable", f"Could not reach the identity provider: {e}") from e


# ------------------------------------------------------------ helpers

def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def callback_url(request):
    return getattr(settings, "SSO_CALLBACK_URL", "") or request.build_absolute_uri("/api/v1/auth/sso/callback/")


def return_to_allowed(url: str) -> bool:
    """Only send the browser back to one of our own frontends (no open redirect)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc or p.path not in ("", "/") or p.query or p.fragment:
        return False
    origin = f"{p.scheme}://{p.netloc}"
    if origin in getattr(settings, "CORS_ALLOWED_ORIGINS", []):
        return True
    return any(re.match(rx, origin) for rx in getattr(settings, "CORS_ALLOWED_ORIGIN_REGEXES", []))


# ------------------------------------------------------------ flow

def start(provider: SSOProvider, request, return_to: str, next_path: str = "") -> str:
    """Returns the identity provider's authorisation URL to redirect to."""
    if not provider.is_enabled:
        raise SSOError("provider_disabled")
    if not return_to_allowed(return_to):
        raise SSOError("bad_return_to", "return_to must be one of this system's own addresses.")
    if next_path and (not next_path.startswith("/") or next_path.startswith("//")):
        next_path = ""
    meta = discovery(provider)
    verifier = _b64url(secrets.token_bytes(48))
    attempt = SSOLoginAttempt.objects.create(
        provider=provider, state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(24), code_verifier=verifier,
        return_to=return_to.rstrip("/"), next_path=next_path[:200],
    )
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": callback_url(request),
        "scope": "openid email profile",
        "state": attempt.state,
        "nonce": attempt.nonce,
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    if provider.kind == SSOProvider.Kind.GOOGLE and len(provider.allowed_domains) == 1:
        params["hd"] = provider.allowed_domains[0]  # Google shows only that Workspace's accounts
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


def _validate_id_token(provider, meta, id_token, nonce):
    try:
        header = jwt.get_unverified_header(id_token)
        keys = _cached(meta["jwks_uri"]).get("keys", [])
        jwk = next((k for k in keys if k.get("kid") == header.get("kid")), None)
        if jwk is None:  # keys rotated — refetch once
            _cache.pop(meta["jwks_uri"], None)
            jwk = next((k for k in _cached(meta["jwks_uri"]).get("keys", []) if k.get("kid") == header.get("kid")), None)
        if jwk is None:
            raise SSOError("invalid_token", "Unknown signing key.")
        issuer = meta["issuer"]
        claims = jwt.decode(
            id_token, jwt.PyJWK(jwk).key, algorithms=["RS256", "RS384", "RS512", "ES256"], audience=provider.client_id,
            issuer=issuer, leeway=60, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except SSOError:
        raise
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise SSOError("invalid_token", f"The sign-in token could not be verified: {e}") from e
    if claims.get("nonce") != nonce:
        raise SSOError("invalid_token", "Nonce mismatch.")
    if provider.kind == SSOProvider.Kind.MICROSOFT and claims.get("tid") != provider.tenant_id:
        raise SSOError("wrong_tenant", "That account belongs to a different organisation.")
    return claims


def _email_from(provider, claims):
    if provider.kind == SSOProvider.Kind.GOOGLE:
        if not claims.get("email_verified"):
            raise SSOError("email_unverified", "The Google account's email is not verified.")
        email = claims.get("email", "")
    else:
        email = claims.get("email") or claims.get("preferred_username") or claims.get("upn") or ""
    email = email.strip().lower()
    if "@" not in email:
        raise SSOError("no_email", "The identity provider did not share an email address.")
    domains = [d.lower().lstrip("@") for d in provider.allowed_domains]
    if domains and email.rsplit("@", 1)[1] not in domains:
        raise SSOError("domain_not_allowed", f"Accounts at {email.rsplit('@', 1)[1]} can't sign in here.")
    if provider.kind == SSOProvider.Kind.GOOGLE and domains and (claims.get("hd") or "").lower() not in domains:
        raise SSOError("domain_not_allowed", "Only this hospital's Google Workspace accounts can sign in.")
    return email


def _account_for(provider, claims, email):
    from .models import User

    identity = SSOIdentity.objects.select_related("user").filter(provider=provider, subject=claims["sub"]).first()
    if identity:
        user = identity.user
    else:
        user = User.objects.filter(hospital_id=provider.hospital_id, email__iexact=email).first()
        if user is None:
            raise SSOError("no_account", f"There is no staff account for {email}. Ask your administrator to add you.")
        if SSOIdentity.objects.filter(provider=provider, user=user).exists():
            raise SSOError("identity_mismatch", "This staff account is linked to a different sign-in identity.")
        identity = SSOIdentity.objects.create(provider=provider, user=user, subject=claims["sub"], email_at_link=email)
    if user.hospital_id != provider.hospital_id or not user.is_active:
        raise SSOError("account_inactive", "This account is not active.")
    if user.is_blocked:
        raise SSOError("account_blocked", "This account has been blocked by an administrator.")
    identity.last_login_at = timezone.now()
    identity.save(update_fields=["last_login_at"])
    return user


def complete(request, state, code, provider_error=""):
    """Handles the provider's redirect back. Returns (attempt, one_time_code) — or raises SSOError
    with `attempt` attached where known so the browser can be sent back to the right frontend."""
    from apps.core.request_utils import get_client_ip
    from apps.governance import services as gov
    from apps.governance.models import SecurityEvent

    attempt = SSOLoginAttempt.objects.select_related("provider").filter(state=state or "-").first()
    if attempt is None or attempt.completed_at or timezone.now() - attempt.created_at > ATTEMPT_TTL:
        raise SSOError("expired", "The sign-in link has expired — please try again.")
    attempt.completed_at = timezone.now()
    provider = attempt.provider
    try:
        if provider_error:
            raise SSOError("provider_error", provider_error)
        if not code:
            raise SSOError("provider_error", "No authorisation code returned.")
        meta = discovery(provider)
        tokens = http_post_form(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(request),
            "client_id": provider.client_id, "client_secret": provider.client_secret, "code_verifier": attempt.code_verifier,
        })
        if not tokens.get("id_token"):
            raise SSOError("invalid_token", "The identity provider returned no ID token.")
        claims = _validate_id_token(provider, meta, tokens["id_token"], attempt.nonce)
        email = _email_from(provider, claims)
        user = _account_for(provider, claims, email)
        if not user.is_login_ip_allowed(get_client_ip(request)):
            gov.log_security_event(SecurityEvent.EventType.IP_BLOCKED, request=request, user=user, details={"method": "sso"})
            raise SSOError("ip_blocked", "Sign-in isn't permitted from this network for this account.")
    except SSOError as e:
        attempt.error = e.code
        attempt.save(update_fields=["completed_at", "error"])
        gov.log_security_event(SecurityEvent.EventType.LOGIN_FAILED, request=request,
                               details={"method": "sso", "provider": provider.display_name, "reason": e.code, "detail": e.detail[:200]})
        e.attempt = attempt
        raise
    one_time = secrets.token_urlsafe(32)
    attempt.user, attempt.exchange_code_hash = user, _hash(one_time)
    attempt.save(update_fields=["completed_at", "user", "exchange_code_hash"])
    gov.reset_failed_logins(user)
    gov.log_security_event(SecurityEvent.EventType.LOGIN_SUCCESS, request=request, user=user, details={"method": "sso", "provider": provider.display_name})
    return attempt, one_time


def exchange(one_time_code):
    """Swaps the one-time code for the same response the password login gives."""
    from .serializers import HospitalScopedTokenObtainPairSerializer, make_mfa_challenge_token

    attempt = SSOLoginAttempt.objects.select_related("user").filter(exchange_code_hash=_hash(one_time_code or "-")).first()
    if attempt is None or attempt.exchanged_at or attempt.user is None or timezone.now() - attempt.completed_at > EXCHANGE_TTL:
        raise SSOError("expired", "The sign-in has expired — please try again.")
    attempt.exchanged_at = timezone.now()
    attempt.save(update_fields=["exchanged_at"])
    user = attempt.user
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    if user.is_2fa_enabled:  # the hospital's own second factor still applies
        return {"mfa_required": True, "mfa_token": make_mfa_challenge_token(user), "next": attempt.next_path}
    refresh = HospitalScopedTokenObtainPairSerializer.get_token(user)
    data = {"refresh": str(refresh), "access": str(refresh.access_token), "next": attempt.next_path}
    if user.requires_mfa:
        data["mfa_setup_required"] = True
    return data


def purge_old_attempts():
    return SSOLoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()[0]
