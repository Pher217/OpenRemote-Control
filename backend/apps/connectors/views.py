"""DRF API views for the connectors MCP bridge.

Provides Start, Notify, Ask, Approve and Result endpoints for authenticated
connector clients, plus an unauthenticated PairClaim endpoint to exchange a
one-time pairing code for an Ed25519-backed connector identity.
"""

import secrets

from django.utils import timezone
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.connectors import service
from apps.connectors.auth import (
    ConnectorBearerAuthentication,
    ConnectorSignatureAuthentication,
    HasConnectorToken,
)
from apps.connectors.models import ConnectorKey, Pairing
from apps.connectors.serializers import (
    ApproveSerializer,
    AskSerializer,
    NotifySerializer,
    StartSerializer,
)


class _ConnectorTokenNotConfigured(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "connector token not configured"
    default_code = "service_unavailable"


class ConnectorBaseView(APIView):
    # ConnectorSignatureAuthentication is checked first; if those headers are
    # absent it returns None and ConnectorBearerAuthentication takes over as
    # the no-op that provides the WWW-Authenticate header for 401 responses.
    authentication_classes = [ConnectorSignatureAuthentication, ConnectorBearerAuthentication]
    permission_classes = [HasConnectorToken]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, "_connector_token_unconfigured", False):
            raise _ConnectorTokenNotConfigured()
        raise exceptions.NotAuthenticated(detail="invalid or missing connector token")


class StartView(ConnectorBaseView):
    def post(self, request):
        serializer = StartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        connector_id = service.resolve_connector_id(request, d["connector_id"])

        result = service.start_session(
            connector_id=connector_id,
            tool=d["tool"],
            workspace_root=d.get("workspace_root", ""),
            name=d.get("name", ""),
        )
        return Response({"ok": True, **result}, status=201)


class NotifyView(ConnectorBaseView):
    def post(self, request):
        serializer = NotifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        # When signature-authenticated, use the server-authoritative connector_id
        # from the key rather than trusting the body (prevents identity spoofing).
        connector_id = service.resolve_connector_id(request, d["connector_id"])

        service.notify(
            connector_id=connector_id,
            tool=d["tool"],
            workspace_root=d.get("workspace_root", ""),
            message=d["message"],
        )
        return Response({"ok": True}, status=200)


class AskView(ConnectorBaseView):
    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        connector_id = service.resolve_connector_id(request, d["connector_id"])

        nonce = service.ask(
            connector_id=connector_id,
            tool=d["tool"],
            workspace_root=d.get("workspace_root", ""),
            question=d["question"],
            options=d.get("options", []),
        )
        return Response({"nonce": nonce, "status": "pending"}, status=201)


class ApproveView(ConnectorBaseView):
    def post(self, request):
        serializer = ApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        connector_id = service.resolve_connector_id(request, d["connector_id"])

        nonce = service.approve(
            connector_id=connector_id,
            tool=d["tool"],
            workspace_root=d.get("workspace_root", ""),
            action=d["action"],
            preview=d.get("preview", ""),
        )
        return Response({"nonce": nonce, "status": "pending"}, status=201)


class ResultView(ConnectorBaseView):
    def get(self, request, nonce):
        data = service.result(nonce)
        return Response(data, status=200)


class PairClaimView(APIView):
    """Claim a pairing code and register an Ed25519 public key.

    No authentication required — the code itself is the single-use secret.
    Body: {code, tool, public_key, label?}
    Returns: {connector_id, key_id} on success.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        code = request.data.get("code", "")
        tool = request.data.get("tool", "")
        public_key = request.data.get("public_key", "")
        label = request.data.get("label", "")

        if not code or not public_key:
            return Response({"detail": "code and public_key are required"}, status=400)

        now = timezone.now()
        try:
            pairing = Pairing.objects.get(code=code)
        except Pairing.DoesNotExist:
            return Response({"detail": "Unknown pairing code"}, status=404)

        if pairing.claimed_at is not None:
            return Response({"detail": "Pairing code already used"}, status=410)

        if not pairing.is_claimable(now):
            return Response({"detail": "Pairing code expired"}, status=410)

        connector_id = f"conn-{secrets.token_hex(6)}"
        key_id = secrets.token_hex(4)

        ConnectorKey.objects.create(
            connector_id=connector_id,
            key_id=key_id,
            public_key=public_key,
            tool=tool or pairing.tool,
            label=label or pairing.label,
            scopes=pairing.scopes,
        )

        pairing.claimed_at = now
        pairing.connector_id = connector_id
        pairing.save(update_fields=["claimed_at", "connector_id"])

        return Response({"connector_id": connector_id, "key_id": key_id}, status=200)
