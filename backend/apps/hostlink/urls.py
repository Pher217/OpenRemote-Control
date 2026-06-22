from django.urls import path

from apps.hostlink.views import (
    EnrollView,
    HostApprovalResultView,
    HostApprovalView,
)

app_name = "hostlink"

urlpatterns = [
    path("enroll", EnrollView.as_view(), name="enroll"),
    path("approve", HostApprovalView.as_view(), name="approve"),
    path("approve/<str:nonce>", HostApprovalResultView.as_view(), name="approve-result"),
]
