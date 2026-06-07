from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.gateway import service
from apps.gateway.auth import GatewayBearerAuthentication, HasGatewayToken

_VALID_PLATFORMS = {"whatsapp", "slack", "discord", "signal", "imessage"}


class _GatewayTokenNotConfigured(exceptions.APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "gateway token not configured"
    default_code = "service_unavailable"


class GatewayBaseView(APIView):
    authentication_classes = [GatewayBearerAuthentication]
    permission_classes = [HasGatewayToken]

    def permission_denied(self, request, message=None, code=None):
        if getattr(request, "_gateway_token_unconfigured", False):
            raise _GatewayTokenNotConfigured()
        raise exceptions.NotAuthenticated(detail="invalid or missing gateway token")


class OutboxView(GatewayBaseView):
    """GET /api/gateway/outbox?platform=<whatsapp|slack|discord|signal|imessage>&max=20"""

    def get(self, request):
        platform = request.query_params.get("platform", "")
        if platform not in _VALID_PLATFORMS:
            return Response(
                {"detail": f"platform must be one of {sorted(_VALID_PLATFORMS)}"},
                status=400,
            )

        try:
            max_count = int(request.query_params.get("max", 20))
        except (ValueError, TypeError):
            max_count = 20
        max_count = min(max(1, max_count), 100)

        messages = service.claim_outbox(platform, max_count)
        return Response({"messages": messages})


class InboundView(GatewayBaseView):
    """POST /api/gateway/inbound  {platform, chat_id, sender, text}"""

    def post(self, request):
        platform = request.data.get("platform", "")
        chat_id = request.data.get("chat_id", "")
        sender = request.data.get("sender", "")
        text = request.data.get("text", "")

        if not platform or not chat_id:
            return Response({"reply": None})

        reply = service.handle_inbound(
            platform=str(platform),
            chat_id=str(chat_id),
            sender=str(sender),
            text=str(text),
        )
        return Response({"reply": reply})
