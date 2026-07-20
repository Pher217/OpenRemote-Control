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
3. **Cross-origin refusal.** Host pinning stops rebinding, but it does nothing
   against a plain cross-origin request to loopback: there the browser sends
   the honest ``Host: localhost:8000``, which passes. So state-changing methods
   additionally require the token in a *header* — which forces a CORS preflight
   the wizard's origin alone can satisfy — and any request self-identifying as
   cross-site via ``Origin``/``Sec-Fetch-Site`` is refused.
4. **One-time token.** The real credential. 256 bits, minted by the installer.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import BasePermission

from apps.setup.models import SESSION_COOKIE_NAME, SetupState, SetupToken

#: Header the wizard's XHRs use. The initial page load uses ``?token=`` instead,
#: since a browser navigation cannot set headers.
TOKEN_HEADER = "HTTP_X_ORC_SETUP_TOKEN"
TOKEN_QUERY_PARAM = "token"

#: Methods that may carry the token in the query string. A state-changing
#: request must use the header: a cross-origin page can issue a "simple"
#: POST to loopback with a query string, but it cannot set a custom header
#: without a preflight it will fail.
QUERY_PARAM_METHODS = frozenset({"GET", "HEAD"})


class SetupClosed(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = "Setup has already been completed."
    default_code = "setup_closed"


def extract_token(request) -> str:
    """Pull the raw token from the header, the session cookie, or (on safe
    methods only) the query param."""
    header = request.META.get(TOKEN_HEADER, "")
    if header:
        return header.strip()
    # Set by the /setup exchange. HttpOnly + SameSite=Strict, so it is neither
    # script-readable nor attached to cross-site requests.
    cookie = request.COOKIES.get(SESSION_COOKIE_NAME, "")
    if cookie:
        return cookie.strip()
    if request.method in QUERY_PARAM_METHODS:
        return request.query_params.get(TOKEN_QUERY_PARAM, "").strip()
    return ""


def normalise_host(raw: str) -> str:
    """Strip the port, brackets, trailing dot and case from a host value.

    Used for both the request's Host and the configured allowlist entries, so
    ``[::1]:8000`` and a configured ``::1`` compare equal — they did not when
    each side was parsed differently.
    """
    host = raw.strip()
    if host.startswith("["):  # bracketed IPv6, optionally with :port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:  # host:port (a bare IPv6 has several colons)
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".").lower()


def host_allowed(request) -> bool:
    """True when the request's Host header is an approved loopback name.

    ``request.get_host()`` is called first so Django's own ``DisallowedHost``
    rejection still applies, but the value compared here comes straight from
    ``HTTP_HOST``. That keeps the gate correct even if a deployment later sets
    ``USE_X_FORWARDED_HOST``, which would otherwise let a remote caller assert
    ``X-Forwarded-Host: localhost`` and walk straight through.
    """
    request.get_host()
    host = normalise_host(request.META.get("HTTP_HOST", ""))
    allowed = {normalise_host(h) for h in settings.ORC_SETUP_ALLOWED_HOSTS}
    return bool(host) and host in allowed


def is_cross_site(request) -> bool:
    """True when the request advertises itself as coming from another origin."""
    fetch_site = request.META.get("HTTP_SEC_FETCH_SITE", "").strip().lower()
    if fetch_site and fetch_site not in ("same-origin", "none"):
        return True
    origin = request.META.get("HTTP_ORIGIN", "").strip()
    if origin:
        # Compare host AND port against the request's own host. Matching on
        # hostname alone would treat http://localhost:3000 — a dev server, or
        # anything else the operator is running on loopback — as same-origin
        # and let it drive setup. "null" (sandboxed iframe, file://) never
        # matches, which is intended.
        parsed = urlparse(origin)
        origin_netloc = (parsed.netloc or "").lower()
        if origin_netloc != request.META.get("HTTP_HOST", "").strip().lower():
            return True
    return False


class SetupTokenPermission(BasePermission):
    """Allow only live-token requests arriving on an allowlisted loopback host."""

    message = "A valid setup token is required."

    def has_permission(self, request, view) -> bool:
        # Host first: a caller that has no business here should not be able to
        # learn whether setup is complete by reading 410 versus 403.
        if not host_allowed(request):
            raise PermissionDenied(
                "The setup wizard is only reachable on localhost. "
                "Do not expose this port publicly."
            )
        # Legitimate wizard traffic is direct browser-to-loopback and is never
        # proxied, so a forwarding header means something is in the path that
        # should not be.
        if request.META.get("HTTP_X_FORWARDED_HOST") or request.META.get("HTTP_X_FORWARDED_FOR"):
            raise PermissionDenied("The setup wizard does not accept proxied requests.")
        if is_cross_site(request):
            raise PermissionDenied("Cross-origin requests to the setup wizard are refused.")
        if SetupState.load().is_complete:
            raise SetupClosed()
        token = SetupToken.verify(extract_token(request))
        if token is None:
            return False
        request.setup_token = token
        return True
