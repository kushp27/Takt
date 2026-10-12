from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Scaffold placeholder: database reachability only. Lane C adds per-hub freshness (eng review D5)."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})
