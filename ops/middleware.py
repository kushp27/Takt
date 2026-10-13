"""Design D13: when the database is unreachable, show a plain 503 page instead of a 500."""

from django.db import InterfaceError, OperationalError
from django.http import HttpResponse
from django.template import loader

from . import freshness


class DatabaseUnavailableMiddleware:
    """Catches database connection failures raised while handling a normal page.

    An OperationalError or InterfaceError is treated as "unreachable" only when a fresh
    `SELECT 1` also fails, so a query timeout or similar one-off error still surfaces as a
    normal 500. The page renders without a request context: no context processors run and
    no database access happens. /healthz handles its own failures and returns JSON 503.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not isinstance(exception, (OperationalError, InterfaceError)):
            return None
        if freshness.database_reachable():
            return None
        return database_unavailable_response()


def database_unavailable_response():
    html = loader.get_template("ops/db_unreachable.html").render()  # no request: no context processors
    response = HttpResponse(html, status=503)
    response["Retry-After"] = "120"
    response["Cache-Control"] = "no-store"
    return response
