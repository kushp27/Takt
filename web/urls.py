"""Screens (Lane D). Every operator-bearing URL here is @operator_bearing and registered in
tests/sweep_registry.py. The area screen is named "area" with kwarg area_id (accounts.scope.own_area_url)."""

from django.urls import path

from . import views
from .screens import line_health

urlpatterns = [
    path("", views.home, name="home"),
    # Line Health (ops manager's home) and its drill-downs
    path("line/", line_health.line_health, name="line_health"),
    path("line/<int:line_id>/stations/", line_health.line_stations, name="line_stations"),
    path("station/<int:station_id>/operators/", line_health.station_operators, name="station_operators"),
]
