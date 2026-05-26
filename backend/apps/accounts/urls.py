from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.accounts.views import AccountViewSet

app_name = "accounts"

router = DefaultRouter()
router.register(r"", AccountViewSet, basename="account")

urlpatterns = [
    path("", include(router.urls)),
]
