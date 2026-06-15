"""Approval request serializers.

DRF ModelSerializer for ApprovalRequest exposing all model fields with
read-only timestamps and signed nonce.
"""
from rest_framework import serializers

from apps.approvals.models import ApprovalRequest


class ApprovalRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalRequest
        fields = [
            "id",
            "thread",
            "request_type",
            "risk",
            "summary",
            "preview",
            "status",
            "requested_at",
            "decided_at",
            "decided_by",
            "expires_at",
            "signed_nonce",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "requested_at", "decided_at", "signed_nonce", "created_at", "updated_at"]
