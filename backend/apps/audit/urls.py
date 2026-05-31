from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.audit.views import AuditEventViewSet

app_name = "audit"

router = DefaultRouter()
router.register(r"", AuditEventViewSet, basename="auditevent")

urlpatterns = [
    path("", include(router.urls)),
]
