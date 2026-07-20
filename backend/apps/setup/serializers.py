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
    """Stages reachable via /advance.

    ``done`` is deliberately excluded: completion must go through
    ``CompleteView``, which is the only path that also burns the setup token.
    Allowing it here would close setup while leaving a live token behind.
    """

    stage = serializers.ChoiceField(
        choices=[c[0] for c in SetupState.STAGE_CHOICES if c[0] != SetupState.STAGE_DONE]
    )
