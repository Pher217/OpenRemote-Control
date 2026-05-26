from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.hosts.views import HostViewSet

app_name = "hosts"

router = DefaultRouter()
router.register(r"", HostViewSet, basename="host")

urlpatterns = [
    path("", include(router.urls)),
]
