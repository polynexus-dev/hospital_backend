from datetime import date
from io import StringIO

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.licensing import crypto
from apps.licensing.licence import build_payload
from tools.license_issuer.cli import main


@pytest.fixture
def key(tmp_path, monkeypatch):
    k = Ed25519PrivateKey.generate()
    path = tmp_path / "signing.pem"
    path.write_text(crypto.private_pem(k))
    monkeypatch.setenv("LICENSE_SIGNING_KEY_PATH", str(path))
    monkeypatch.delenv("LICENSE_SIGNING_KEY", raising=False)
    return k


def run(argv):
    out = StringIO()
    return main(argv, out=out), out.getvalue()


def licence_file(key, tmp_path):
    payload = build_payload(customer_name="Clinic", deployment_id="3f2b8c1e-5d4a-4e6b-9c7d-1a2b3c4d5e6f",
                            starts_on=date(2026, 10, 10), expires_on=date(2027, 10, 9), max_active_users=10,
                            features=["hms_core"], issued_by="PNX-0002", approved_by="PNX-0001")
    path = tmp_path / "x.lic"
    path.write_text(crypto.seal(payload, key))
    return path


def test_verify_shows_who_issued_it(key, tmp_path):
    code, output = run(["verify", str(licence_file(key, tmp_path))])
    assert code == 0 and "Signature OK" in output
    assert "Issued by       : PNX-0002  (approved by PNX-0001)" in output


def test_verify_rejects_a_tampered_file(key, tmp_path):
    lic = licence_file(key, tmp_path)
    lic.write_text(lic.read_text()[:-12] + "AAAAAAAAAAA=")
    assert run(["verify", str(lic)])[0] == 1


@pytest.mark.parametrize("command", [["new"], ["renew", "x.lic"]])
def test_issuing_is_only_in_the_saas_console(command, key):
    code, output = run(command)
    assert code == 2 and "SaaS console" in output
