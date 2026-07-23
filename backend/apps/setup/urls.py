from django.urls import path

from apps.setup.views import (
    AdvanceView,
    CompleteView,
    StateView,
    TelegramDiscoverView,
    TelegramTokenView,
)

app_name = "orc_setup"

urlpatterns = [
    path("state", StateView.as_view(), name="state"),
    path("advance", AdvanceView.as_view(), name="advance"),
    path("complete", CompleteView.as_view(), name="complete"),
    path("telegram/token", TelegramTokenView.as_view(), name="telegram-token"),
    path("telegram/discover", TelegramDiscoverView.as_view(), name="telegram-discover"),
]
