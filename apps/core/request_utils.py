import hmac

from django.conf import settings


def webhook_secret_ok(request) -> bool:
    """Authenticates an otherwise-public webhook (telephony/IVR provider,
    inbound WhatsApp/SMS/email relay) against the deployment's configured
    WEBHOOK_SHARED_SECRET, presented as the X-Webhook-Secret header or a
    `?secret=` query parameter (whichever the provider can send in its
    configured callback URL).

    These endpoints accept an unauthenticated body that creates call/message
    records and fires automation, so anyone who can reach the URL could forge
    them. When a secret is configured it is required (constant-time compare).
    When none is configured the webhook is refused in production and allowed
    only in DEBUG, so a real deployment fails closed instead of trusting the
    internet, while local dev and the test suite keep working without setup."""
    expected = getattr(settings, "WEBHOOK_SHARED_SECRET", "") or ""
    if not expected:
        return bool(getattr(settings, "DEBUG", False))
    presented = request.headers.get("X-Webhook-Secret") or request.query_params.get("secret") or ""
    return bool(presented) and hmac.compare_digest(str(presented), expected)


def get_client_ip(request):
    """Best-effort client IP, honoring a reverse proxy's X-Forwarded-For.
    Shared by apps.core.middleware.AuditMiddleware and the login serializer's
    allowed_ip_ranges check so both agree on what "the client's IP" means.
    Returns None (not "") when unknown — an empty string is invalid input
    for GenericIPAddressField/Postgres inet, where None is simply NULL.

    X-Forwarded-For is a client-supplied header: nginx *appends* the real
    peer to whatever the client sent ($proxy_add_x_forwarded_for), so the
    left-most entry is attacker-controlled and must never be trusted. With
    NUM_PROXIES trusted hops in front of the app (nginx = 1), the genuine
    client is the NUM_PROXIES-th entry from the right. Taking [0] let a
    request forge its source IP and so bypass the login rate-limit, the
    failed-login lockout attribution, and per-account allowed_ip_ranges
    (User.is_login_ip_allowed). This mirrors DRF's own throttle identity
    (REST_FRAMEWORK["NUM_PROXIES"]), so both agree."""
    remote_addr = request.META.get("REMOTE_ADDR") or None
    num_proxies = getattr(settings, "NUM_PROXIES", 1)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if not forwarded or not num_proxies:
        # No proxy in front (NUM_PROXIES=0), or no header: the TCP peer is the client.
        return remote_addr
    addrs = [a.strip() for a in forwarded.split(",") if a.strip()]
    if not addrs:
        return remote_addr
    return addrs[-min(num_proxies, len(addrs))]
