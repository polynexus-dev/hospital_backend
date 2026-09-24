class AccessDeniedLoggingMiddleware:
    """DOM.3.a "unauthorized access attempts" — every 403 an authenticated
    user receives from the API becomes a SecurityEvent. Placed after
    AuditMiddleware; reads request.user after the response, by which point
    DRF has propagated the JWT-authenticated user onto the Django request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code == 403 and request.path.startswith("/api/"):
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                from .models import SecurityEvent
                from .services import log_security_event

                log_security_event(
                    SecurityEvent.EventType.ACCESS_DENIED, request=request, user=user,
                    details={"method": request.method},
                )
        return response
