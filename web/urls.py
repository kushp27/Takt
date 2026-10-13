"""Screens (Lane D). Every operator-bearing URL here is @operator_bearing and registered in
tests/sweep_registry.py. The area screen is named "area" with kwarg area_id (accounts.scope.own_area_url)."""

from django.urls import path

from disputes import views as dispute_views

from . import views
from .screens import area, line_health

urlpatterns = [
    path("", views.home, name="home"),
    # Line Health (ops manager's home) and its drill-downs
    path("line/", line_health.line_health, name="line_health"),
    path("line/<int:line_id>/stations/", line_health.line_stations, name="line_stations"),
    path("station/<int:station_id>/operators/", line_health.station_operators, name="station_operators"),
    # Area (supervisor's home) and the dispute queue
    path("area/", views.area_home, name="area_home"),
    path("area/<int:area_id>/", area.area, name="area"),
    path("disputes/", views.disputes_home, name="disputes_home"),
    path("area/<int:area_id>/disputes/", dispute_views.dispute_queue, name="dispute_queue"),
    path("area/<int:area_id>/disputes/<int:dispute_id>/", dispute_views.dispute_detail, name="dispute_detail"),
    path("area/<int:area_id>/disputes/<int:dispute_id>/decide/", dispute_views.dispute_decide,
         name="dispute_decide"),
]
