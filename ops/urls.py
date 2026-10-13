from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("freshness/notice", views.freshness_notice, name="freshness_notice"),
]
