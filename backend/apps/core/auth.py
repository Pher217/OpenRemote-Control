"""Shared bearer-token permission base for ORC apps.

Plain-Python utility — no models, not in INSTALLED_APPS.
"""
import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class BearerTokenPermission(BasePermission):
    """Base bearer-token gate parameterised by settings attribute and request flag.

    Subclasses must set:
      settings_attr  — name of the Django settings attribute holding the expected token
      unconfigured_flag — name of the request attribute set when the token is empty
    """

    settings_attr: str
    unconfigured_flag: str

    def _get_token(self) -> str:
        return getattr(settings, self.settings_attr, "")

    def has_permission(self, request, view) -> bool:
        expected = self._get_token()
        if not expected:
            setattr(request, self.unconfigured_flag, True)
            return False

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.lower().startswith("bearer "):
            return False

        provided = auth_header[len("bearer "):].strip()
        return hmac.compare_digest(provided.encode(), expected.encode())
