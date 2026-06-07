from .base import *

DEBUG = False

# TLS is terminated by the reverse proxy (Caddy), which forwards
# X-Forwarded-Proto. Django must trust it, otherwise SECURE_SSL_REDIRECT below
# would 301-loop every (already-HTTPS) request and the internal /health/ probe.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
# Internal liveness probes hit /health/ over plain HTTP (no X-Forwarded-Proto),
# so exempt it from the HTTPS redirect — otherwise the container healthcheck 301s.
SECURE_REDIRECT_EXEMPT = [r"^health/?$"]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
]
