"""SSO: a fake identity provider with a real RSA key, so every validation
path (signature, issuer, audience, expiry, nonce, tenant) runs for real."""
import time
from urllib.parse import parse_qs, unquote, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from rest_framework.test import APIClient

from apps.accounts import sso
from apps.accounts.models import Role, SSOIdentity, SSOProvider, User, assign_role
from apps.governance.models import SecurityEvent, SecurityPolicy

TENANT = "11111111-2222-3333-4444-555555555555"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"
FRONTEND = "http://localhost:5173"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid):
    d = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**d, "kid": kid, "use": "sig", "alg": "RS256"}


class FakeIdP:
    def __init__(self, issuer=ISSUER):
        self.issuer = issuer
        self.next_claims = {}
        self.sign_with = KEY
        self.posted = []

    def get_json(self, url):
        if url.endswith("openid-configuration"):
            return {"issuer": self.issuer, "authorization_endpoint": "https://idp.example/authorize",
                    "token_endpoint": "https://idp.example/token", "jwks_uri": "https://idp.example/keys"}
        if url.endswith("/keys"):
            return {"keys": [_jwk(KEY, "k1")]}
        raise AssertionError(url)

    def post_form(self, url, data):
        self.posted.append(data)
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": data["client_id"], "sub": "sub-asha", "iat": now, "exp": now + 600,
                  "nonce": self.nonce, "tid": TENANT, "email": "asha@cityhospital.in", **self.next_claims}
        return {"id_token": jwt.encode(claims, self.sign_with, algorithm="RS256", headers={"kid": "k1"}), "access_token": "x"}


@pytest.fixture
def idp(monkeypatch):
    sso._cache.clear()
    fake = FakeIdP()
    monkeypatch.setattr(sso, "http_get_json", fake.get_json)
    monkeypatch.setattr(sso, "http_post_form", fake.post_form)
    return fake


@pytest.fixture
def provider(hospital):
    return SSOProvider.objects.create(hospital=hospital, kind="microsoft", display_name="Microsoft", client_id="client-123",
                                      client_secret="s3cret", tenant_id=TENANT, allowed_domains=["cityhospital.in"])


@pytest.fixture
def nurse(hospital, department):
    u = User.objects.create_user(email="asha@cityhospital.in", password="Str0ng-Pass-123!", hospital=hospital, department=department)
    assign_role(u, Role.objects.create(hospital=hospital, department=department, name="Nurse", template=Role.Template.NURSE))
    return u


def sign_in(idp, client=None, **claims):
    """Runs start → IdP → callback; returns the callback redirect."""
    client = client or APIClient()
    pid = SSOProvider.objects.get().pk
    start = client.get(f"/api/v1/auth/sso/{pid}/start/", {"return_to": FRONTEND, "next": "/dashboard"})
    assert start.status_code == 302, getattr(start, "data", start)
    q = parse_qs(urlparse(start["Location"]).query)
    idp.nonce = q["nonce"][0]
    idp.next_claims = claims
    return client.get("/api/v1/auth/sso/callback/", {"code": "auth-code", "state": q["state"][0]}), q


def fragment(resp):
    frag = urlparse(resp["Location"]).fragment
    key, _, value = frag.partition("=")
    return key, unquote(value)


@pytest.mark.django_db
def test_full_sign_in_links_identity_and_issues_tokens_once(idp, provider, nurse):
    listing = APIClient().get("/api/v1/auth/sso/providers/", {"hospital": nurse.hospital.slug})
    assert listing.data == {"providers": [{"id": provider.pk, "name": "Microsoft", "kind": "microsoft"}], "password_login": True}

    cb, q = sign_in(idp)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"][0].endswith("/api/v1/auth/sso/callback/")
    assert idp.posted[0]["code_verifier"] and idp.posted[0]["client_secret"] == "s3cret"
    assert cb.status_code == 302 and cb["Location"].startswith(f"{FRONTEND}/login#sso=")
    kind, code = fragment(cb)
    tokens = APIClient().post("/api/v1/auth/sso/exchange/", {"code": code}, format="json")
    assert tokens.status_code == 200 and tokens.data["next"] == "/dashboard"
    assert jwt.decode(tokens.data["access"], options={"verify_signature": False})["user_id"] == str(nurse.pk)
    assert APIClient().post("/api/v1/auth/sso/exchange/", {"code": code}, format="json").status_code == 400  # one use
    assert SSOIdentity.objects.get(user=nurse).subject == "sub-asha"
    assert SecurityEvent.objects.filter(event_type="login_success", user=nurse, details__method="sso").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("claims,error", [
    ({"aud": "someone-else"}, "invalid_token"),
    ({"nonce": "replayed"}, "invalid_token"),
    ({"exp": int(time.time()) - 3600}, "invalid_token"),
    ({"tid": "99999999-0000-0000-0000-000000000000"}, "wrong_tenant"),
    ({"email": "asha@gmail.com"}, "domain_not_allowed"),
    ({"email": "nobody@cityhospital.in", "sub": "sub-nobody"}, "no_account"),
])
def test_rejected_sign_ins_come_back_as_errors(idp, provider, nurse, claims, error):
    cb, _ = sign_in(idp, **claims)
    assert fragment(cb) == ("sso_error", error)
    assert not SSOIdentity.objects.exists()
    assert SecurityEvent.objects.filter(event_type="login_failed", details__reason=error).exists()


@pytest.mark.django_db
def test_forged_signature_and_state_replay_and_open_redirect(idp, provider, nurse):
    idp.sign_with = OTHER_KEY
    cb, q = sign_in(idp)
    assert fragment(cb) == ("sso_error", "invalid_token")
    replay = APIClient().get("/api/v1/auth/sso/callback/", {"code": "auth-code", "state": q["state"][0]})
    assert replay.status_code == 400  # a state can only be used once
    evil = APIClient().get(f"/api/v1/auth/sso/{provider.pk}/start/", {"return_to": "https://evil.example"})
    assert evil.status_code == 400 and evil.data["code"] == "bad_return_to"


@pytest.mark.django_db
def test_identity_binding_blocks_a_recycled_email(idp, provider, nurse):
    sign_in(idp)  # links sub-asha
    cb, _ = sign_in(idp, sub="sub-new-person")  # same email, different person at the IdP
    assert fragment(cb) == ("sso_error", "identity_mismatch")


@pytest.mark.django_db
def test_blocked_user_and_local_2fa(idp, provider, nurse):
    nurse.is_blocked = True
    nurse.save()
    cb, _ = sign_in(idp)
    assert fragment(cb) == ("sso_error", "account_blocked")

    nurse.is_blocked, nurse.is_2fa_enabled = False, True
    nurse.save()
    cb, _ = sign_in(idp)
    res = APIClient().post("/api/v1/auth/sso/exchange/", {"code": fragment(cb)[1]}, format="json")
    assert res.data["mfa_required"] is True and "access" not in res.data


@pytest.mark.django_db
def test_google_requires_verified_workspace_email(idp, hospital, nurse, monkeypatch):
    SSOProvider.objects.create(hospital=hospital, kind="google", display_name="Google", client_id="g-client", client_secret="g",
                               allowed_domains=["cityhospital.in"])
    idp.issuer = "https://accounts.google.com"
    cb, q = sign_in(idp, email_verified=False)
    assert q["hd"] == ["cityhospital.in"]
    assert fragment(cb) == ("sso_error", "email_unverified")
    cb, _ = sign_in(idp, email_verified=True, hd="othercorp.com")
    assert fragment(cb) == ("sso_error", "domain_not_allowed")
    cb, _ = sign_in(idp, email_verified=True, hd="cityhospital.in")
    assert fragment(cb)[0] == "sso"


@pytest.mark.django_db
def test_sso_required_refuses_staff_passwords_but_keeps_admin_break_glass(provider, nurse, user, hospital):
    SecurityPolicy.objects.update_or_create(hospital=hospital, defaults={"sso_required": True})
    res = APIClient().post("/api/v1/auth/login/", {"email": nurse.email, "password": "Str0ng-Pass-123!"}, format="json")
    assert res.status_code == 401 and res.data["code"] == "sso_required"
    admin = APIClient().post("/api/v1/auth/login/", {"email": user.email, "password": "testpass123"}, format="json")
    assert admin.status_code == 200 and "access" in admin.data  # owner/admin roles keep password sign-in
    assert APIClient().get("/api/v1/auth/sso/providers/", {"hospital": hospital.slug}).data["password_login"] is False


@pytest.mark.django_db
def test_admin_api_hides_secret_and_validates(auth_client):
    bad = auth_client.post("/api/v1/sso-providers/", {"kind": "microsoft", "display_name": "Microsoft", "client_id": "c", "client_secret": "s"}, format="json")
    assert bad.status_code == 400 and "tenant_id" in bad.data
    ok = auth_client.post("/api/v1/sso-providers/", {"kind": "microsoft", "display_name": "Microsoft", "client_id": "c", "client_secret": "s",
                                                     "tenant_id": TENANT, "allowed_domains": ["@CityHospital.in"]}, format="json")
    assert ok.status_code == 201 and "client_secret" not in ok.data and ok.data["has_secret"] is True
    assert ok.data["allowed_domains"] == ["cityhospital.in"]
    assert SSOProvider.objects.get().client_secret == "s"  # stored (encrypted at rest), never returned
    assert auth_client.get("/api/v1/sso-providers/setup/").data["redirect_uri"].endswith("/api/v1/auth/sso/callback/")


@pytest.mark.django_db
def test_staff_without_admin_rights_cannot_see_sso_config(restricted_client):
    assert restricted_client.get("/api/v1/sso-providers/").status_code == 403


@pytest.mark.django_db
def test_hr_manager_cannot_manage_sso_despite_user_management_rights(hospital, department):
    hr = User.objects.create_user(email="hr@cityhospital.in", password="Str0ng-Pass-123!", hospital=hospital, department=department)
    assign_role(hr, Role.objects.create(hospital=hospital, department=department, name="HR", template=Role.Template.HR_MANAGER))
    client = APIClient()
    client.force_authenticate(hr)
    assert client.get("/api/v1/sso-providers/").status_code == 403
    assert client.post("/api/v1/sso-providers/", {"kind": "google", "display_name": "G", "client_id": "c", "client_secret": "s"}, format="json").status_code == 403
