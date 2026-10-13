"""Test-only urlconf: the real site (takt.urls) plus the sweep demo views."""

from takt.urls import urlpatterns as _real
from tests.sweep_demo import demo_urlpatterns

urlpatterns = [*demo_urlpatterns, *_real]
