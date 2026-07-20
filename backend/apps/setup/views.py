"""Phase-1 setup endpoints: read wizard state, advance it, close it out.

Provider- and runtime-specific flows (Telegram discovery, gateway QR pairing,
runtime detection) land in later phases; this module is deliberately just the
state machine's HTTP surface.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.setup.auth import SetupClosed, SetupTokenPermission
from apps.setup.models import SetupState, SetupToken
from apps.setup.serializers import AdvanceSerializer, SetupStateSerializer


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
