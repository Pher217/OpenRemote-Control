from django.urls import path

from apps.setup.views import AdvanceView, CompleteView, StateView

app_name = "orc_setup"

urlpatterns = [
    path("state", StateView.as_view(), name="state"),
    path("advance", AdvanceView.as_view(), name="advance"),
    path("complete", CompleteView.as_view(), name="complete"),
]
