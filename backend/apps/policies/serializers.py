from rest_framework import serializers

from apps.policies.models import PolicyProfile


class PolicyProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyProfile
        fields = [
            "id",
            "name",
            "sensitivity_max",
            "runtime_modes_allowed",
            "providers_allowed",
            "provider_jurisdictions_allowed",
            "account_orgs_allowed",
            "hosts_allowed",
            "egress_allowed",
            "rc_via_anthropic_allowed",
            "cloud_models_allowed",
            "data_classes_allowed",
            "raw_retention_max_days",
            "require_worktree",
            "require_approval_for",
            "block_destructive",
            "max_runtime_minutes",
            "max_parallel_threads",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
