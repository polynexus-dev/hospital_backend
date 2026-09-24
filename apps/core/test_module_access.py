"""Subscription enforcement: API calls to a module the hospital hasn't
licensed are refused. Uses real JWTs (as the browser does) — the check runs
before DRF's own authentication."""
import pytest
from django.core import signing
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import ALL_MODULES
from apps.core.modules import VIEW_MODULES


def bearer(user):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return c


def set_modules(hospital, modules):
    hospital.enabled_modules = modules
    hospital.save(update_fields=["enabled_modules"])


def test_every_licensable_module_is_enforced():
    assert set(VIEW_MODULES.values()) == set(ALL_MODULES)


@pytest.mark.django_db
def test_disabled_module_is_refused_shared_features_stay_open(user, hospital):
    set_modules(hospital, ["opd", "enquiries"])
    c = bearer(user)
    res = c.get("/api/v1/cathlab/procedures/")
    assert res.status_code == 403
    assert res.json() == {"detail": "The cathlab module isn't part of this hospital's subscription.", "code": "module_disabled", "module": "cathlab"}
    assert c.get("/api/v1/predict/staffing/").json().get("module") == "predictive"
    assert c.get("/api/v1/tpa/claims/").json().get("module") == "tpa"  # CRM modules too
    assert c.get("/api/v1/patients/").status_code == 200  # shared by both suites
    assert c.get("/api/v1/enquiries/").status_code == 200  # licensed

    set_modules(hospital, ["opd", "enquiries", "cathlab"])
    assert c.get("/api/v1/cathlab/procedures/").status_code == 200


@pytest.mark.django_db
def test_empty_list_means_everything(user, hospital):
    set_modules(hospital, [])
    assert bearer(user).get("/api/v1/cathlab/procedures/").status_code == 200


@pytest.mark.django_db
def test_platform_operators_are_not_limited(saas_admin_user, hospital):
    set_modules(hospital, ["opd"])
    res = bearer(saas_admin_user).get("/api/v1/cathlab/procedures/")
    assert res.status_code != 403 or res.json().get("code") != "module_disabled"


@pytest.mark.django_db
def test_patient_portal_and_public_screens_follow_the_subscription(hospital):
    from apps.portal.views import TOKEN_SALT
    from apps.queue_mgmt.models import QueueDisplay

    set_modules(hospital, ["opd"])
    anon = APIClient()
    otp = anon.post("/api/v1/portal/auth/request-otp/", {"hospital": hospital.slug, "mobile": "9876500071"}, format="json")
    assert otp.status_code == 403 and otp.json()["code"] == "module_disabled"

    portal = APIClient()
    portal.credentials(HTTP_AUTHORIZATION="Portal " + signing.dumps({"h": str(hospital.pk), "m": "x"}, salt=TOKEN_SALT))
    assert portal.get("/api/v1/portal/me/").json().get("module") == "portal"

    display = QueueDisplay.objects.create(hospital=hospital, name="OPD TV")
    board = anon.get(f"/api/v1/queue/public/board/{display.key}/")
    assert board.status_code == 403 and board.json()["module"] == "queue"

    set_modules(hospital, ["opd", "queue"])
    assert anon.get(f"/api/v1/queue/public/board/{display.key}/").status_code == 200
