def get_client_ip(request):
    """Best-effort client IP, honoring a reverse proxy's X-Forwarded-For.
    Shared by apps.core.middleware.AuditMiddleware and the login serializer's
    allowed_ip_ranges check so both agree on what "the client's IP" means.
    Returns None (not "") when unknown — an empty string is invalid input
    for GenericIPAddressField/Postgres inet, where None is simply NULL."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None
