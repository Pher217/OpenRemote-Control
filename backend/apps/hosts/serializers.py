from rest_framework import serializers

from apps.hosts.models import Host


class HostSerializer(serializers.ModelSerializer):
    class Meta:
        model = Host
        fields = [
            "id",
            "slug",
            "name",
            "os",
            "tailscale_dns",
            "last_seen_at",
            "status",
            "capabilities",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
