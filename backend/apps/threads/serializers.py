from rest_framework import serializers

from apps.threads.models import Message, Thread


class ThreadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Thread
        fields = [
            "id",
            "name",
            "runtime",
            "runtime_mode",
            "host",
            "account",
            "project",
            "status",
            "external_session_ref",
            "worktree_path",
            "branch_name",
            "started_at",
            "last_event_at",
            "ended_at",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "thread",
            "role",
            "redacted_content",
            "sequence",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
