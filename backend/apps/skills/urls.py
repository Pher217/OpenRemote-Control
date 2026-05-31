from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.skills.views import SkillViewSet

app_name = "skills"

router = DefaultRouter()
router.register(r"", SkillViewSet, basename="skill")

urlpatterns = [
    path("", include(router.urls)),
]
