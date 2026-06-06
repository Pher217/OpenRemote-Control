from django.urls import path

from apps.hostlink.views import EnrollView

app_name = "hostlink"

urlpatterns = [
    path("enroll", EnrollView.as_view(), name="enroll"),
]
