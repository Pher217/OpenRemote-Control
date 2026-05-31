from rest_framework import viewsets

from apps.approvals.models import ApprovalRequest
from apps.approvals.serializers import ApprovalRequestSerializer


class ApprovalRequestViewSet(viewsets.ModelViewSet):
    queryset = ApprovalRequest.objects.all()
    serializer_class = ApprovalRequestSerializer
