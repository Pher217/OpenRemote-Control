"""Minimal URL conf for gateway tests — includes the gateway namespace."""
from django.urls import include, path

urlpatterns = [
    path("api/gateway/", include("apps.gateway.urls", namespace="gateway")),
]
