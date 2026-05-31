from rest_framework import serializers

from apps.projects.models import Project


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            "id",
            "slug",
            "name",
            "repo_url",
            "sensitivity",
            "policy",
            "local_paths",
            "allowed_accounts",
            "allowed_hosts",
            "allowed_runtimes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
