from rest_framework import viewsets

from apps.skills.models import Skill
from apps.skills.serializers import SkillSerializer


class SkillViewSet(viewsets.ModelViewSet):
    queryset = Skill.objects.all()
    serializer_class = SkillSerializer
