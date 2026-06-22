"""DRF authentication for host-daemon HTTP calls.

The daemon already authenticates its websocket with its per-host token
(``HostToken``). This lets it authenticate HTTP calls the same way, via
``Authorization: Bearer <token>``. The authenticated principal (``request.user``)
is the ``Host``.
"""

from rest_framework import authentication, exceptions, permissions

from apps.hostlink.models import HostToken


class HostTokenAuthentication(authentication.BaseAuthentication):
    """Authenticate a host daemon by its per-host bearer token."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode()
        if not header.startswith(self.keyword + " "):
            return None  # no host-token header → let other authenticators try / 401
        raw_token = header[len(self.keyword) + 1 :].strip()
        if not raw_token:
            raise exceptions.AuthenticationFailed("invalid host token")
        host = HostToken.authenticate(raw_token)
        if host is None:
            raise exceptions.AuthenticationFailed("invalid or revoked host token")
        return (host, raw_token)

    def authenticate_header(self, request) -> str:
        # Presence of this header makes DRF return 401 (not 403) when unauthenticated.
        return self.keyword


class IsAuthenticatedHost(permissions.BasePermission):
    """Allow only requests carrying a valid host token (request.auth set).

    The DRF default ``IsAuthenticated`` assumes a Django user with
    ``is_authenticated``; our principal is a ``Host``, so we gate on the token.
    """

    def has_permission(self, request, view) -> bool:
        return request.auth is not None
