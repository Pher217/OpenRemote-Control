"""Bearer-token authentication and permission classes for the gateway app.

The messaging-gateway sidecar authenticates with a shared secret provided
via the MESSAGING_GATEWAY_TOKEN setting.
"""

import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission


def _get_token() -> str:
    return getattr(settings, "MESSAGING_GATEWAY_TOKEN", "")


class GatewayBearerAuthentication(BaseAuthentication):
    """No-op authenticator that advertises the Bearer challenge.

    The actual token check lives in HasGatewayToken. Registering an
    authenticator with an `authenticate_header` causes DRF to return 401
    (not 403) when the permission denies an unauthenticated request.
    """

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class HasGatewayToken(BasePermission):
    """Bearer token gate for the messaging gateway endpoints.

    Returns False (→ 503) if MESSAGING_GATEWAY_TOKEN is unset.
    Returns False (→ 401) if the header is absent or wrong.
    """

    def has_permission(self, request, view) -> bool:
        expected = _get_token()
        if not expected:
            request._gateway_token_unconfigured = True
            return False

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.lower().startswith("bearer "):
            return False

        provided = auth_header[len("bearer "):].strip()
        return hmac.compare_digest(provided.encode(), expected.encode())
