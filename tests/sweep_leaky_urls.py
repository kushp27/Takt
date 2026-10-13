"""Test-only urlconf for the harness's own self-test: real site + demo + a deliberately leaky,
unregistered view and a marked view with no sweep case. Discovery must flag both."""

from django.urls import path

from accounts.decorators import operator_bearing
from tests.sweep_demo import leaky_unregistered, leaky_urlpatterns
from tests.sweep_urls import urlpatterns as _base

urlpatterns = [
    *leaky_urlpatterns,
    path("_sweep-demo/marked-no-case/", operator_bearing(leaky_unregistered), name="sweep_demo_marked_no_case"),
    *_base,
]
