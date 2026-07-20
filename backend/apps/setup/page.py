"""The ``/setup`` landing page and the token-for-cookie exchange.

The installer opens ``/setup?token=<raw>``. Leaving that token in the address
bar means it also lives in browser history, in the access log, and in any
``Referer`` a later subresource sends. So the first load trades it for an
HttpOnly cookie and redirects to a clean ``/setup``:

1. The presented token is verified.
2. A **new** token is minted, which revokes the presented one — so the value
   sitting in history and logs is dead within milliseconds of being used.
3. The new token goes into an HttpOnly, SameSite=Strict cookie. HttpOnly keeps
   it away from any script on the page; SameSite=Strict means the browser will
   not attach it to a cross-site request at all, which is a stronger CSRF
   defense than the header requirement it backs up.
4. A 303 lands the browser on ``/setup`` with no query string.

The page itself is intentionally a stub: it reports state so the flow is
verifiable end to end. The real wizard UI replaces this body in phase 2.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.views import View

from apps.setup.auth import host_allowed, is_cross_site
from apps.setup.models import SESSION_COOKIE_NAME, SetupState, SetupToken


class SetupPageView(View):
    """Serve the wizard shell, exchanging a URL token for a cookie on arrival."""

    def get(self, request):
        # Host/origin before state, so a caller from somewhere else cannot read
        # 410-vs-403 to learn whether this installation is already set up.
        if not host_allowed(request) or is_cross_site(request):
            return render(request, "setup/denied.html", status=403)
        state = SetupState.load()
        if state.is_complete:
            return render(request, "setup/complete.html", status=410)

        raw = request.GET.get("token", "").strip()
        if raw:
            if SetupToken.verify(raw) is None:
                return render(request, "setup/denied.html", status=403)
            # Rotate: minting revokes the token that was in the URL.
            _, fresh = SetupToken.issue()
            response = HttpResponseRedirect(request.path)
            response.set_cookie(
                SESSION_COOKIE_NAME,
                fresh,
                httponly=True,
                samesite="Strict",
                secure=request.is_secure(),
                path="/",
                max_age=int(settings.ORC_SETUP_TOKEN_TTL_MINUTES) * 60,
            )
            return response

        if SetupToken.verify(request.COOKIES.get(SESSION_COOKIE_NAME, "")) is None:
            return render(request, "setup/denied.html", status=403)

        return render(request, "setup/index.html", {"state": state})
