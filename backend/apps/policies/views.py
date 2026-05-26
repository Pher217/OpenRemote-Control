from rest_framework import viewsets

from apps.policies.models import PolicyProfile
from apps.policies.serializers import PolicyProfileSerializer


class PolicyProfileViewSet(viewsets.ModelViewSet):
    queryset = PolicyProfile.objects.all()
    serializer_class = PolicyProfileSerializer
