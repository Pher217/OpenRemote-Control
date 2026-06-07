from django.urls import path

from apps.connectors.views import (
    ApproveView,
    AskView,
    NotifyView,
    PairClaimView,
    ResultView,
    StartView,
)

app_name = "connectors"

urlpatterns = [
    path("start", StartView.as_view(), name="start"),
    path("notify", NotifyView.as_view(), name="notify"),
    path("ask", AskView.as_view(), name="ask"),
    path("approve", ApproveView.as_view(), name="approve"),
    path("result/<str:nonce>", ResultView.as_view(), name="result"),
    path("pair/claim", PairClaimView.as_view(), name="pair-claim"),
]
