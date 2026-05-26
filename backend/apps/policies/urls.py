from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.policies.views import PolicyProfileViewSet

app_name = "policies"

router = DefaultRouter()
router.register(r"", PolicyProfileViewSet, basename="policyprofile")

urlpatterns = [
    path("", include(router.urls)),
]
