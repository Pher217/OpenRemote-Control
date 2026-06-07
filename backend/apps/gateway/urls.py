from django.urls import path

from apps.gateway.views import InboundView, OutboxView

app_name = "gateway"

urlpatterns = [
    path("outbox", OutboxView.as_view(), name="outbox"),
    path("inbound", InboundView.as_view(), name="inbound"),
]
