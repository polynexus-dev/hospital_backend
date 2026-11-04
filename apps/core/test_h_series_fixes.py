"""Regression tests for the H1 / H4 fixes.

H1  the client IP comes from the trusted-proxy position in X-Forwarded-For,
    so a forged left-most entry cannot spoof the source IP (which would
    defeat the login throttle, lockout, and per-account IP allow-list)
H4  the provider webhooks authenticate against WEBHOOK_SHARED_SECRET
"""
from types import SimpleNamespace

from django.test import override_settings

from apps.core.request_utils import get_client_ip, webhook_secret_ok


def _req(xff=None, remote="203.0.113.7", headers=None, query=None):
    meta = {"REMOTE_ADDR": remote}
    if xff is not None:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    return SimpleNamespace(META=meta, headers=headers or {}, query_params=query or {})


# ---- H1 -------------------------------------------------------------------

@override_settings(NUM_PROXIES=1)
def test_h1_forged_left_most_xff_does_not_win():
    # Client forged "1.2.3.4"; nginx appended the real peer "9.9.9.9".
    assert get_client_ip(_req(xff="1.2.3.4, 9.9.9.9")) == "9.9.9.9"


@override_settings(NUM_PROXIES=1)
def test_h1_no_header_uses_peer():
    assert get_client_ip(_req(xff=None, remote="198.51.100.5")) == "198.51.100.5"


@override_settings(NUM_PROXIES=0)
def test_h1_num_proxies_zero_ignores_xff():
    assert get_client_ip(_req(xff="1.2.3.4", remote="198.51.100.5")) == "198.51.100.5"


@override_settings(NUM_PROXIES=2)
def test_h1_two_trusted_proxies():
    # client, edge-lb, nginx  -> genuine client is 2nd from the right
    assert get_client_ip(_req(xff="evil, 7.7.7.7, 8.8.8.8")) == "7.7.7.7"


# ---- H4 -------------------------------------------------------------------

@override_settings(WEBHOOK_SHARED_SECRET="s3cr3t", DEBUG=False)
def test_h4_secret_required_when_configured():
    assert webhook_secret_ok(_req()) is False  # nothing presented
    assert webhook_secret_ok(_req(headers={"X-Webhook-Secret": "wrong"})) is False
    assert webhook_secret_ok(_req(headers={"X-Webhook-Secret": "s3cr3t"})) is True
    assert webhook_secret_ok(_req(query={"secret": "s3cr3t"})) is True


@override_settings(WEBHOOK_SHARED_SECRET="", DEBUG=False)
def test_h4_unset_secret_fails_closed_in_production():
    assert webhook_secret_ok(_req()) is False


@override_settings(WEBHOOK_SHARED_SECRET="", DEBUG=True)
def test_h4_unset_secret_allowed_in_dev():
    assert webhook_secret_ok(_req()) is True
