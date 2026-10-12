"""Several authenticator apps per account (phone, backup phone, tablet)."""
import pyotp
import pytest


def add_device(client, name, existing_secret=None):
    body = {"name": name}
    if existing_secret:
        body["otp"] = pyotp.TOTP(existing_secret).now()
    setup = client.post("/api/v1/users/2fa/setup/", body, format="json")
    assert setup.status_code == 200, setup.data
    secret = setup.data["secret"]
    assert client.post("/api/v1/users/2fa/enable/", {"otp": pyotp.TOTP(secret).now()}, format="json").status_code == 200
    return secret


def login_with(api_client, user, code):
    login = api_client.post("/api/v1/auth/login/", {"email": user.email, "password": "testpass123"}, format="json")
    return api_client.post("/api/v1/auth/mfa/verify/", {"mfa_token": login.data["mfa_token"], "otp": code}, format="json")


@pytest.mark.django_db
def test_codes_from_any_device_sign_in(api_client, auth_client, user):
    phone = add_device(auth_client, "My phone")
    backup = add_device(auth_client, "Backup phone", existing_secret=phone)

    devices = auth_client.get("/api/v1/users/2fa/devices/").data
    assert [d["name"] for d in devices] == ["My phone", "Backup phone"]
    assert "secret" not in devices[0]

    assert login_with(api_client, user, pyotp.TOTP(phone).now()).status_code == 200
    assert login_with(api_client, user, pyotp.TOTP(backup).now()).status_code == 200


@pytest.mark.django_db
def test_adding_another_device_needs_a_code_from_an_existing_one(auth_client):
    add_device(auth_client, "My phone")
    res = auth_client.post("/api/v1/users/2fa/setup/", {"name": "Thief's phone"}, format="json")
    assert res.status_code == 400 and "existing authenticator" in res.data["otp"]


@pytest.mark.django_db
def test_removing_a_lost_phone_keeps_the_others(api_client, auth_client, user):
    phone = add_device(auth_client, "Lost phone")
    backup = add_device(auth_client, "Backup phone", existing_secret=phone)
    lost_id = auth_client.get("/api/v1/users/2fa/devices/").data[0]["id"]

    assert auth_client.post(f"/api/v1/users/2fa/devices/{lost_id}/remove/", {"password": "wrong"}, format="json").status_code == 400
    res = auth_client.post(f"/api/v1/users/2fa/devices/{lost_id}/remove/", {"password": "testpass123"}, format="json")
    assert res.data["is_2fa_enabled"] is True

    assert login_with(api_client, user, pyotp.TOTP(phone).now()).status_code == 400  # the lost phone no longer works
    assert login_with(api_client, user, pyotp.TOTP(backup).now()).status_code == 200


@pytest.mark.django_db
def test_removing_the_last_device_turns_2fa_off(auth_client, user):
    add_device(auth_client, "Only phone")
    only_id = auth_client.get("/api/v1/users/2fa/devices/").data[0]["id"]
    res = auth_client.post(f"/api/v1/users/2fa/devices/{only_id}/remove/", {"password": "testpass123"}, format="json")
    assert res.data["is_2fa_enabled"] is False
    user.refresh_from_db()
    assert user.is_2fa_enabled is False


@pytest.mark.django_db
def test_an_unconfirmed_device_is_never_accepted(api_client, auth_client, user):
    phone = add_device(auth_client, "My phone")
    pending = auth_client.post("/api/v1/users/2fa/setup/", {"name": "Not finished", "otp": pyotp.TOTP(phone).now()}, format="json").data["secret"]
    assert login_with(api_client, user, pyotp.TOTP(pending).now()).status_code == 400
    assert len(auth_client.get("/api/v1/users/2fa/devices/").data) == 1
