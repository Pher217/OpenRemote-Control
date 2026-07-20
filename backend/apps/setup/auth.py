"""The gate in front of every ``/api/setup/*`` route.

Three independent checks, in order of cheapness:

1. **Setup closed?** Once the wizard has completed, the whole surface returns
   410 Gone regardless of credentials. Re-opening it requires an operator with
   shell access (``manage.py setup_token``), never a network caller.
2. **Host header allowlist.** The wizard is reachable only on loopback, but
   loopback services are still attackable from a browser via DNS rebinding —
   the attacker's page keeps its own origin while the resolved address flips to
   127.0.0.1. Pinning the ``Host`` header defeats that, and unlike a
   ``REMOTE_ADDR`` check it survives Docker (the browser sends
   ``Host: localhost:8000``; ``REMOTE_ADDR`` would be the bridge gateway).
3. **One-time token.** The real credential. 256 bits, minted by the installer,
   never guessable and never readable cross-origin.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import BasePermission

from apps.setup.models import SetupState, SetupToken

#: Header the wizard's XHRs use. The initial page load uses ``?token=`` instead,
#: since a browser navigation cannot set headers.
TOKEN_HEADER = "HTTP_X_ORC_SETUP_TOKEN"
TOKEN_QUERY_PARAM = "token"


class SetupClosed(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = "Setup has already been completed."
    default_code = "setup_closed"


def extract_token(request) -> str:
    """Pull the raw token from the header, falling back to the query param."""
    header = request.META.get(TOKEN_HEADER, "")
    if header:
        return header.strip()
    return request.query_params.get(TOKEN_QUERY_PARAM, "").strip()


def host_allowed(request) -> bool:
    """True when the request's Host header is an approved loopback name."""
    host = request.get_host().split(":")[0].lower()
    allowed = {h.lower() for h in settings.ORC_SETUP_ALLOWED_HOSTS}
    return host in allowed


class SetupTokenPermission(BasePermission):
    """Allow only live-token requests arriving on an allowlisted loopback host."""

    message = "A valid setup token is required."

    def has_permission(self, request, view) -> bool:
        if SetupState.load().is_complete:
            raise SetupClosed()
        if not host_allowed(request):
            raise PermissionDenied(
                "The setup wizard is only reachable on localhost. "
                "Do not expose this port publicly."
            )
        token = SetupToken.verify(extract_token(request))
        if token is None:
            return False
        request.setup_token = token
        return True
