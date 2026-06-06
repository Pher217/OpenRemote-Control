from rest_framework import serializers


class EnrollSerializer(serializers.Serializer):
    enroll_secret = serializers.CharField(max_length=256)
    hostname = serializers.CharField(max_length=255)
    os = serializers.CharField(max_length=16)
    hw_uuid = serializers.CharField(max_length=255)
