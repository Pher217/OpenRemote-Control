import os

from django.core.exceptions import ImproperlyConfigured

from .base import *

DEBUG = False

# --- Fail closed on unset secrets -----------------------------------------
# base.py provides development fallbacks so the test suite and a bare `import
# config.settings` work without a populated environment.  In production those
# fallbacks are dangerous: a deploy that forgets SECRET_KEY would silently run
# with a value that is public in this repository, breaking session, CSRF and
# signed-cookie integrity.  Refuse to boot instead.
_INSECURE_DEFAULTS = {
    "SECRET_KEY": "dev-key-do-not-use-in-production",
    "POSTGRES_PASSWORD": "acc_password",
}

for _name, _dev_default in _INSECURE_DEFAULTS.items():
    _value = os.environ.get(_name)
    if not _value or _value == _dev_default:
        raise ImproperlyConfigured(
            f"{_name} must be set to a real value in production settings "
            f"(it is unset or still the development default). "
            f"Generate one and put it in your deploy environment file."
        )

# TLS is terminated by the reverse proxy (Caddy), which forwards
# X-Forwarded-Proto. Django must trust it, otherwise SECURE_SSL_REDIRECT below
# would 301-loop every (already-HTTPS) request and the internal /health/ probe.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
# Internal liveness probes hit /health/ over plain HTTP (no X-Forwarded-Proto),
# so exempt it from the HTTPS redirect — otherwise the container healthcheck 301s.
# The setup wizard is reached over plain HTTP on loopback (the installer opens
# http://127.0.0.1:8000/setup?token=…) and its own gate refuses any Host that is
# not localhost, so it can never be served over a network where plaintext would
# matter. Without this exemption that URL 301s to https://127.0.0.1:8000, where
# nothing is listening, and first-run setup is simply broken in production.
SECURE_REDIRECT_EXEMPT = [r"^health/?$", r"^setup/?$", r"^api/setup/"]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
]
