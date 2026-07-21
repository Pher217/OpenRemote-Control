import re

from rest_framework import serializers

from apps.setup.models import SetupState

#: Telegram bot tokens are `<numeric bot id>:<35-char secret>`. The token is
#: interpolated raw into the request URL path in telegram_flow._call, so a
#: value containing "/" or "?" would rewrite the request path, and a value
#: with control characters can raise httpx.InvalidURL. Rejecting anything
#: that doesn't match Telegram's real token shape closes both off before the
#: value ever reaches httpx.
_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{10,}$")


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


class TelegramTokenSerializer(serializers.Serializer):
    """A bot token pasted by the operator.

    All whitespace is stripped (mirrors quickstart.sh's ``tr -d '[:space:]'``)
    since a token copied from a chat app commonly picks up a trailing newline.
    """

    token = serializers.CharField()

    def validate_token(self, value: str) -> str:
        stripped = "".join(value.split())
        if not stripped:
            raise serializers.ValidationError("token must not be empty")
        if not _TOKEN_RE.match(stripped):
            # Never echo the value back — it's a credential being rejected.
            raise serializers.ValidationError("token is not a valid Telegram bot token")
        return stripped
