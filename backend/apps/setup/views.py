"""Phase-1 setup endpoints: read wizard state, advance it, close it out.

Provider- and runtime-specific flows (Telegram discovery, gateway QR pairing,
runtime detection) land in later phases; this module is deliberately just the
state machine's HTTP surface.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.setup import telegram_flow
from apps.setup.auth import SetupClosed, SetupTokenPermission
from apps.setup.env_writer import EnvWriteError, read_env, update_env
from apps.setup.models import SetupState, SetupToken
from apps.setup.serializers import AdvanceSerializer, SetupStateSerializer, TelegramTokenSerializer


class SetupAPIView(APIView):
    """Base for every setup route — token-gated, never session-authenticated."""

    authentication_classes: list = []
    permission_classes = [SetupTokenPermission]


class StateView(SetupAPIView):
    """GET the current wizard state."""

    def get(self, request):
        return Response(SetupStateSerializer(SetupState.load()).data)


class AdvanceView(SetupAPIView):
    """POST to move the wizard to the next stage."""

    def post(self, request):
        serializer = AdvanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        state = SetupState.load()
        try:
            state.advance_to(serializer.validated_data["stage"])
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SetupStateSerializer(state).data)


class CompleteView(SetupAPIView):
    """POST to finish setup: closes the stage machine and burns the token.

    Both writes happen in one transaction, and the token row is locked first.
    Splitting them would leave a recoverable-only-by-shell state: if closing
    setup committed and burning the token did not, every retry would 410 while
    the token stayed live. The lock also stops two concurrent completions from
    both spending the same single-use token.
    """

    def post(self, request):
        try:
            with transaction.atomic():
                token = SetupToken.objects.select_for_update().get(pk=request.setup_token.pk)
                if not token.is_live():
                    raise SetupClosed()
                state = SetupState.objects.select_for_update().get(pk=SetupState.load().pk)
                if state.is_complete:
                    raise SetupClosed()
                state.advance_to(SetupState.STAGE_DONE)
                token.consume()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SetupStateSerializer(state).data)


class ProvidersStageView(SetupAPIView):
    """Base for routes that only make sense while connecting chat providers.

    Provider configuration writes security-relevant keys — the default-deny
    ``TELEGRAM_ALLOWED_CHAT_IDS`` above all. Once the wizard has moved past the
    providers stage the operator has left that screen, so a request arriving
    here is either a stale tab or someone else's; either way it must not be
    able to rewrite the allowlist. The token gate alone does not express this:
    the token stays live until CompleteView burns it.
    """

    def _stage_guard(self):
        state = SetupState.load()
        if state.stage != SetupState.STAGE_PROVIDERS:
            return state, Response(
                {"detail": "Setup is no longer connecting chat providers."},
                status=status.HTTP_409_CONFLICT,
            )
        return state, None


class TelegramTokenView(ProvidersStageView):
    """POST a freshly pasted bot token: validate it, then persist it.

    Only ``TELEGRAM_BOT_TOKEN`` is written here. The provider is not marked
    connected yet — the operator's chat is not known until discovery below
    finds it. A fresh challenge code is minted and returned so the page can
    display it; discovery will not accept a group that has not echoed it.
    """

    def post(self, request):
        state, blocked = self._stage_guard()
        if blocked is not None:
            return blocked
        serializer = TelegramTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = serializer.validated_data["token"]
        try:
            result = telegram_flow.get_me(token)
        except telegram_flow.TelegramError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            update_env(Path(settings.ORC_SETUP_ENV_FILE), {"TELEGRAM_BOT_TOKEN": token})
        except EnvWriteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # Minted only after the token is known good and stored: a challenge
        # issued beside a rejected token would be shown on a page whose
        # discovery step cannot run, and re-validating would silently rotate
        # the code out from under a group message already sent.
        challenge = state.issue_telegram_challenge()
        username = result["username"]
        return Response(
            {
                "username": username,
                "bot_link": f"https://t.me/{username}",
                "challenge": challenge,
            }
        )


class TelegramDiscoverView(ProvidersStageView):
    """POST to poll getUpdates for the operator's group message.

    The token is read back from the env FILE rather than ``settings`` —
    settings were loaded at process boot and will not see a token
    :class:`TelegramTokenView` just wrote.

    Discovery is bound to the challenge code minted at token time. Without
    that binding this endpoint is a privilege-escalation hole: bot usernames
    are public, so anyone may add the bot to a group of their own and message
    it during the detection window, landing their id in the default-deny
    allowlist. No challenge on record means the flow is out of order — fail
    closed rather than falling back to "first group message wins".
    """

    def post(self, request):
        state, blocked = self._stage_guard()
        if blocked is not None:
            return blocked
        env_path = Path(settings.ORC_SETUP_ENV_FILE)
        token = read_env(env_path).get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return Response(
                {"detail": "Connect a bot token first."}, status=status.HTTP_400_BAD_REQUEST
            )
        challenge = state.telegram_challenge
        if not challenge:
            return Response(
                {"detail": "Validate the bot token first to get a confirmation code."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            found = telegram_flow.discover_chat(token, challenge)
        except telegram_flow.TelegramError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if found is None:
            return Response({"found": False})
        # discover_chat only returns matches carrying a numeric sender id, so
        # this should be unreachable. It is checked anyway because the failure
        # mode is silent and severe: writing the chat keys without
        # TELEGRAM_ALLOWED_CHAT_IDS leaves a chat wired up behind an allowlist
        # that was never populated. Refusing beats a half-configured provider.
        if not found["user_id"]:
            return Response(
                {"detail": "Could not identify the sender of that message. Send it again."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        updates = {
            "TELEGRAM_FORUM_CHAT_ID": found["chat_id"],
            "ORC_PROMPT_CHAT_ID": found["chat_id"],
            "ORC_MESSAGING_PLATFORM": "telegram",
            "TELEGRAM_ALLOWED_CHAT_IDS": found["user_id"],
        }
        try:
            update_env(env_path, updates)
        except EnvWriteError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # Burn the challenge only after the env write succeeded — a failed
        # write leaves the operator able to retry with the code already shown.
        state.clear_telegram_challenge()
        state.set_provider("telegram", "connected")
        return Response(
            {
                "found": True,
                "chat_id": found["chat_id"],
                "title": found["title"],
                "is_forum": found["is_forum"],
                "user_id": found["user_id"],
            }
        )
