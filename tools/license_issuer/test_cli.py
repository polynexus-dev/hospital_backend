from datetime import date
from io import StringIO

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.licensing import crypto
from tools.license_issuer.cli import main

FP = "b" * 64


@pytest.fixture
def key(tmp_path, monkeypatch):
    k = Ed25519PrivateKey.generate()
    path = tmp_path / "signing.pem"
    path.write_text(crypto.private_pem(k))
    monkeypatch.setenv("LICENSE_SIGNING_KEY_PATH", str(path))
    monkeypatch.delenv("LICENSE_SIGNING_KEY", raising=False)
    return k


def run(argv, answers=None):
    out = StringIO()
    code = main(argv, answers=answers, out=out)
    return code, out.getvalue()


def test_new_wizard_writes_a_signed_licence(key, tmp_path):
    out_file = tmp_path / "apollo.lic"
    answers = [
        "Apollo Clinic Mumbai",          # name
        "",                              # deployment id -> generated
        "2026-10-10", "2027-10-09",      # start, expiry
        "",                              # grace -> 14
        "50",                            # max users
        "1,2", "", # toggle HMS core + CRM, then done
        "y", FP,                         # hardware binding + fingerprint
        str(out_file),
    ]
    code, output = run(["new"], answers)
    assert code == 0, output
    payload = crypto.open_license(out_file.read_text(), key.public_key())
    assert payload["customer_name"] == "Apollo Clinic Mumbai"
    assert payload["features"] == ["hms_core", "crm"]
    assert payload["max_active_users"] == 50 and payload["grace_period_days"] == 14
    assert payload["hardware_binding"] is True and payload["machine_fingerprint"] == FP
    assert payload["expires_at"].startswith("2027-10-09") and len(payload["deployment_id"]) == 36
    assert "[x] CRM" in output and "DEPLOYMENT_ID=" in output


def test_wizard_rejects_bad_dates_and_numbers(key, tmp_path):
    answers = ["Clinic", "", "10/10/2026", "2026-10-10", "2026-01-01", "2027-01-01", "x", "5", "-1", "3", "", "n", str(tmp_path / "c.lic")]
    code, output = run(["new"], answers)
    assert code == 0, output
    assert "Use the format YYYY-MM-DD" in output and "Must be after 2026-10-10" in output
    assert "Enter a whole number" in output and "Must be at least 1" in output


def test_verify_and_renew_keep_the_licence_id(key, tmp_path):
    lic = tmp_path / "x.lic"
    run(["new", "--quiet"], ["Clinic", "", "2026-10-10", "2027-10-09", "", "10", "", "n", str(lic)])
    original = crypto.open_license(lic.read_text(), key.public_key())

    code, output = run(["verify", str(lic)])
    assert code == 0 and "Signature OK" in output

    renewed_file = tmp_path / "renewed.lic"
    code, output = run(["renew", str(lic)], ["2028-10-09", str(renewed_file)])
    assert code == 0, output
    renewed = crypto.open_license(renewed_file.read_text(), key.public_key())
    assert renewed["license_id"] == original["license_id"]
    assert renewed["expires_at"].startswith("2028-10-09")
    assert renewed["issued_at"] >= original["issued_at"]


def test_tampered_file_and_missing_key(key, tmp_path, monkeypatch):
    lic = tmp_path / "x.lic"
    run(["new", "--quiet"], ["Clinic", "", "2026-10-10", "2027-10-09", "", "10", "", "n", str(lic)])
    lic.write_text(lic.read_text()[:-12] + "AAAAAAAAAAA=")
    assert run(["verify", str(lic)])[0] == 1

    monkeypatch.delenv("LICENSE_SIGNING_KEY_PATH")
    code, output = run(["new"], [])
    assert code == 1 and "No signing key" in output
