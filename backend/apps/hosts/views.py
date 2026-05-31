from rest_framework import viewsets

from apps.hosts.models import Host
from apps.hosts.serializers import HostSerializer


class HostViewSet(viewsets.ModelViewSet):
    queryset = Host.objects.all()
    serializer_class = HostSerializer
