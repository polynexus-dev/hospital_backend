from django.db import connection
from django.http import JsonResponse


def health(request):
    """GET /api/health/ — liveness for container healthchecks. No auth, no
    data: just "the app is up and can reach its database"."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "database unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
