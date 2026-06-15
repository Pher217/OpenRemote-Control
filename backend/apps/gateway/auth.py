"""Bearer-token authentication and permission classes for the gateway app.

The messaging-gateway sidecar authenticates with a shared secret provided
via the MESSAGING_GATEWAY_TOKEN setting.
"""

from rest_framework.authentication import BaseAuthentication

from apps.core.auth import BearerTokenPermission


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


class HasGatewayToken(BearerTokenPermission):
    """Bearer token gate for the messaging gateway endpoints.

    Returns False (→ 503) if MESSAGING_GATEWAY_TOKEN is unset.
    Returns False (→ 401) if the header is absent or wrong.
    """

    settings_attr = "MESSAGING_GATEWAY_TOKEN"
    unconfigured_flag = "_gateway_token_unconfigured"
