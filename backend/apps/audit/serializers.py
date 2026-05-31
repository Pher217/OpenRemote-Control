from rest_framework import serializers

from apps.audit.models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "timestamp",
            "thread",
            "actor",
            "event_type",
            "redacted_payload",
        ]
        read_only_fields = ["id", "timestamp"]
