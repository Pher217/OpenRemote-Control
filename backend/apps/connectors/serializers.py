"""DRF serializers for incoming connector bridge requests.

Defines validation schemas for the notify/start/ask/approve chat-surface
operations that route messages between coding-agent sessions and connectors.
"""
from rest_framework import serializers


class NotifySerializer(serializers.Serializer):
    connector_id = serializers.CharField(max_length=255)
    tool = serializers.CharField(max_length=64)
    workspace_root = serializers.CharField(max_length=1024, default="", allow_blank=True)
    message = serializers.CharField()


class StartSerializer(serializers.Serializer):
    connector_id = serializers.CharField(max_length=255)
    tool = serializers.CharField(max_length=64)
    workspace_root = serializers.CharField(max_length=1024, default="", allow_blank=True)
    name = serializers.CharField(max_length=255, default="", allow_blank=True)
    # The caller's own coding-session id (e.g. CLAUDE_CODE_SESSION_ID). When set,
    # the driveable thread binds to it so Telegram replies `--resume` THIS session.
    claude_session_id = serializers.CharField(
        max_length=255, default="", allow_blank=True
    )
    provider = serializers.CharField(max_length=32, default='claude', allow_blank=True)
    hostname = serializers.CharField(max_length=255, default="", allow_blank=True)


class AskSerializer(serializers.Serializer):
    connector_id = serializers.CharField(max_length=255)
    tool = serializers.CharField(max_length=64)
    workspace_root = serializers.CharField(max_length=1024, default="", allow_blank=True)
    question = serializers.CharField(max_length=500)
    options = serializers.ListField(
        child=serializers.CharField(max_length=255),
        default=list,
        allow_empty=True,
    )


class ApproveSerializer(serializers.Serializer):
    connector_id = serializers.CharField(max_length=255)
    tool = serializers.CharField(max_length=64)
    workspace_root = serializers.CharField(max_length=1024, default="", allow_blank=True)
    action = serializers.CharField(max_length=500)
    preview = serializers.CharField(default="", allow_blank=True)
