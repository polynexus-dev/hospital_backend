"""The browser app's refresh token travels only in an httpOnly cookie
(apps.accounts.refresh_cookie) — never in a body a page script can read."""
import pytest
from django.conf import settings

from apps.accounts.models import User

COOKIE = {"HTTP_X_AUTH_COOKIE": "1"}
EMAIL, PASSWORD = "cookie-test@test-hospital.example", "correct-horse-1"


@pytest.fixture
def staff(hospital, department):
    return User.objects.create_user(email=EMAIL, password=PASSWORD, hospital=hospital, department=department)


def _login(api_client, **extra):
    return api_client.post("/api/v1/auth/login/", {"email": EMAIL, "password": PASSWORD, **extra}, format="json", **COOKIE)


@pytest.mark.django_db
def test_login_sets_an_httponly_cookie_and_leaves_refresh_out_of_the_body(api_client, staff):
    response = _login(api_client)

    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" not in response.data
    cookie = response.cookies[settings.REFRESH_COOKIE_NAME]
    assert cookie.value
    assert cookie["httponly"]
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth/"
    assert int(cookie["max-age"]) == int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())


@pytest.mark.django_db
def test_keep_me_signed_in_unchecked_gives_a_browser_session_cookie(api_client, staff):
    response = _login(api_client, remember=False)

    assert response.cookies[settings.REFRESH_COOKIE_NAME]["max-age"] == ""
    # ...and a rotation keeps it a session cookie
    refreshed = api_client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE)
    assert refreshed.status_code == 200
    assert refreshed.cookies[settings.REFRESH_COOKIE_NAME]["max-age"] == ""


@pytest.mark.django_db
def test_cookie_is_secure_over_https(api_client, staff):
    response = api_client.post("/api/v1/auth/login/", {"email": EMAIL, "password": PASSWORD}, format="json", secure=True, **COOKIE)
    assert response.cookies[settings.REFRESH_COOKIE_NAME]["secure"]


@pytest.mark.django_db
def test_refresh_reads_the_cookie_and_rotates_it(api_client, staff):
    first = _login(api_client).cookies[settings.REFRESH_COOKIE_NAME].value

    response = api_client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE)

    assert response.status_code == 200
    assert "access" in response.data and "refresh" not in response.data
    rotated = response.cookies[settings.REFRESH_COOKIE_NAME].value
    assert rotated and rotated != first
    # the old token was blacklisted on rotation
    api_client.cookies[settings.REFRESH_COOKIE_NAME] = first
    assert api_client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE).status_code == 401


@pytest.mark.django_db
def test_the_cookie_is_ignored_without_the_opt_in_header(api_client, staff):
    """The custom header is the CSRF guard: a cross-site form or fetch can't
    send it without a CORS preflight, so the cookie alone is never enough."""
    _login(api_client)
    response = api_client.post("/api/v1/auth/refresh/", {}, format="json")
    assert response.status_code == 401


@pytest.mark.django_db
def test_a_rejected_cookie_is_cleared(api_client, staff):
    api_client.cookies[settings.REFRESH_COOKIE_NAME] = "not-a-token"
    response = api_client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE)
    assert response.status_code == 401
    assert response.cookies[settings.REFRESH_COOKIE_NAME].value == ""


@pytest.mark.django_db
def test_logout_blacklists_the_cookie_token_and_clears_the_cookie(api_client, staff):
    token = _login(api_client).cookies[settings.REFRESH_COOKIE_NAME].value

    logout = api_client.post("/api/v1/auth/logout/", {}, format="json", **COOKIE)

    assert logout.status_code == 200
    assert logout.cookies[settings.REFRESH_COOKIE_NAME].value == ""
    api_client.cookies[settings.REFRESH_COOKIE_NAME] = token
    assert api_client.post("/api/v1/auth/refresh/", {}, format="json", **COOKIE).status_code == 401


@pytest.mark.django_db
def test_clients_without_the_header_keep_the_token_in_the_body(api_client, staff):
    """Mobile app, Postman and integrations are unchanged."""
    response = api_client.post("/api/v1/auth/login/", {"email": EMAIL, "password": PASSWORD}, format="json")
    assert response.data["refresh"]
    assert settings.REFRESH_COOKIE_NAME not in response.cookies

    refreshed = api_client.post("/api/v1/auth/refresh/", {"refresh": response.data["refresh"]}, format="json")
    assert refreshed.status_code == 200 and refreshed.data["refresh"]


@pytest.mark.django_db
def test_api_json_responses_carry_a_strict_content_security_policy(api_client, staff):
    response = _login(api_client)
    assert response["Content-Security-Policy"].startswith("default-src 'none'")
