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
