from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.threads.views import ThreadViewSet

app_name = "threads"

router = DefaultRouter()
router.register(r"", ThreadViewSet, basename="thread")

urlpatterns = [
    path("", include(router.urls)),
]
