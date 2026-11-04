"""
The browser app's refresh token lives in an httpOnly cookie, never in the
response body or in localStorage (OWASP A07 / WASA: a single XSS bug must
not hand over a 12-hour account takeover). The access token stays in the
body: it is short-lived and the SPA keeps it in memory only.

Opt-in per request with the `X-Auth-Cookie: 1` header. Callers that don't
send it (the patient mobile app, Postman, integrations) keep receiving and
sending `refresh` in the JSON body exactly as before.

The header doubles as the CSRF guard for the cookie: a custom header makes
any cross-origin request a CORS preflight, which only allowed origins pass,
so another site can't make the browser spend the cookie on its behalf.
SameSite and a path limited to the auth endpoints narrow it further.
"""
from django.conf import settings

OPT_IN_HEADER = "X-Auth-Cookie"


def wants_cookie(request) -> bool:
    return request.headers.get(OPT_IN_HEADER) == "1"


def read(request) -> str:
    """The refresh token from the cookie, only for a caller that opted in."""
    return request.COOKIES.get(settings.REFRESH_COOKIE_NAME, "") if wants_cookie(request) else ""


def _secure(request) -> bool:
    # "auto": Secure whenever the browser reached us over HTTPS (behind the
    # proxy, via SECURE_PROXY_SSL_HEADER). On-premise installs serve plain
    # HTTP until a certificate is mounted, and a Secure cookie would never
    # come back there; the cookie is still httpOnly, which is the XSS point.
    if settings.REFRESH_COOKIE_SECURE == "auto":
        return request.is_secure()
    return settings.REFRESH_COOKIE_SECURE == "true"


def _remember(request, remember) -> bool:
    if remember is not None:
        return bool(remember) and str(remember).lower() not in ("0", "false")
    # a rotation keeps whatever the sign-in chose
    return request.COOKIES.get(settings.REFRESH_COOKIE_PERSIST_NAME) != "0"


def issue(request, response, *, remember=None):
    """Moves `refresh` from the response body into the cookie when the
    caller opted in. remember=False (login's "Keep me signed in" left
    unchecked) makes it a browser-session cookie."""
    data = getattr(response, "data", None)
    if not wants_cookie(request) or not isinstance(data, dict) or "refresh" not in data:
        return response
    token = data.pop("refresh")
    keep = _remember(request, remember)
    max_age = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()) if keep else None
    for name, value in ((settings.REFRESH_COOKIE_NAME, token), (settings.REFRESH_COOKIE_PERSIST_NAME, "1" if keep else "0")):
        response.set_cookie(
            name, value, max_age=max_age, path=settings.REFRESH_COOKIE_PATH,
            secure=_secure(request), httponly=True, samesite=settings.REFRESH_COOKIE_SAMESITE,
        )
    return response


def clear(response):
    for name in (settings.REFRESH_COOKIE_NAME, settings.REFRESH_COOKIE_PERSIST_NAME):
        response.delete_cookie(name, path=settings.REFRESH_COOKIE_PATH, samesite=settings.REFRESH_COOKIE_SAMESITE)
    return response
