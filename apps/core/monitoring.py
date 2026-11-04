"""
Error monitoring (WASA / ISO 27001 A.8.16): unhandled exceptions go to a
Sentry-protocol server when SENTRY_DSN is set (config/settings/base.py).
Point it at a self-hosted GlitchTip or Sentry and nothing leaves our own
infrastructure.

Whatever the server, an event carries no patient data: send_default_pii is
off, local variables are not captured, and scrub_event below drops request
bodies, cookies, query strings and every header but a short allow-list —
the same rule as this codebase's logging convention (never log request
payloads).
"""

_KEPT_HEADERS = {"user-agent", "content-type", "x-forwarded-proto"}


def scrub_event(event, hint=None):
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in _KEPT_HEADERS}
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    return event


def init_sentry(*, dsn, environment, release, traces_sample_rate):
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        before_send=scrub_event,
        traces_sample_rate=traces_sample_rate,
    )
