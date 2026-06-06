import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission


def _get_token() -> str:
    return getattr(settings, "ORC_CONNECTOR_TOKEN", "")


class ConnectorBearerAuthentication(BaseAuthentication):
    """No-op authenticator that only advertises the Bearer challenge.

    The actual token check lives in HasConnectorToken. Registering an
    authenticator with an `authenticate_header` makes DRF return 401 (not 403)
    when the permission denies an unauthenticated request.
    """

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class HasConnectorToken(BasePermission):
    """Bearer token gate for the connector bridge endpoints.

    Rejects with 503 if ORC_CONNECTOR_TOKEN is not configured,
    and with 401 if the header is missing or the token does not match.
    Uses constant-time comparison to prevent timing attacks.
    """

    def has_permission(self, request, view) -> bool:
        expected = _get_token()
        if not expected:
            # Surface as 503; handled in views via permission denial + custom
            # error response — see ConnectorBaseView.
            request._connector_token_unconfigured = True
            return False

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.lower().startswith("bearer "):
            return False

        provided = auth_header[len("bearer "):].strip()
        return hmac.compare_digest(provided.encode(), expected.encode())
