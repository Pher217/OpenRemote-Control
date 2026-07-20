from rest_framework import serializers

from apps.setup.models import SetupState


class SetupStateSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)
    connected_providers = serializers.ListField(read_only=True)

    class Meta:
        model = SetupState
        fields = [
            "stage",
            "providers",
            "runtimes",
            "connected_providers",
            "is_complete",
            "completed_at",
            "updated_at",
        ]
        read_only_fields = fields


class AdvanceSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=[c[0] for c in SetupState.STAGE_CHOICES])
