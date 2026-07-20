"""Phase-1 setup endpoints: read wizard state, advance it, close it out.

Provider- and runtime-specific flows (Telegram discovery, gateway QR pairing,
runtime detection) land in later phases; this module is deliberately just the
state machine's HTTP surface.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.setup.auth import SetupTokenPermission
from apps.setup.models import SetupState
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
    """POST to finish setup: closes the stage machine and burns the token."""

    def post(self, request):
        state = SetupState.load()
        try:
            state.advance_to(SetupState.STAGE_DONE)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        request.setup_token.consume()
        return Response(SetupStateSerializer(state).data)
