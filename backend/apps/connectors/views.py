from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.connectors import service
from apps.connectors.auth import ConnectorBearerAuthentication, HasConnectorToken
from apps.connectors.serializers import ApproveSerializer, AskSerializer, NotifySerializer


class _ConnectorTokenNotConfigured(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "connector token not configured"
    default_code = "service_unavailable"


class ConnectorBaseView(APIView):
    authentication_classes = [ConnectorBearerAuthentication]
    permission_classes = [HasConnectorToken]

    def permission_denied(self, request, message=None, code=None):
        # If ORC_CONNECTOR_TOKEN is empty, the service is misconfigured — 503.
        if getattr(request, "_connector_token_unconfigured", False):
            raise _ConnectorTokenNotConfigured()
        # Otherwise the header is missing or wrong — 401.
        raise exceptions.NotAuthenticated(detail="invalid or missing connector token")


class NotifyView(ConnectorBaseView):
    def post(self, request):
        serializer = NotifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        service.notify(
            connector_id=d["connector_id"],
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

        nonce = service.ask(
            connector_id=d["connector_id"],
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

        nonce = service.approve(
            connector_id=d["connector_id"],
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
