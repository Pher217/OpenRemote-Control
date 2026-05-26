from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.approvals.views import ApprovalRequestViewSet

app_name = "approvals"

router = DefaultRouter()
router.register(r"", ApprovalRequestViewSet, basename="approvalrequest")

urlpatterns = [
    path("", include(router.urls)),
]
