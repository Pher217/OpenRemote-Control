"""Authentication and permission classes for the connectors bridge.

Supports ed25519 request-signature authentication per connector key plus a
legacy shared bearer token fallback, and routes principal checks for the
MCP chat-surface bridge.
"""
import time

from django.core.cache import cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.connectors.crypto import verify_signature
from apps.connectors.models import ConnectorKey
from apps.core.auth import BearerTokenPermission


class ConnectorSignatureAuthentication(BaseAuthentication):
    """Ed25519 per-connector request signature authentication (UC0).

    A connector attaches five X-ORC-* headers to every request:
      X-ORC-Connector-Id  — the connector's stable identity
      X-ORC-Key-Id        — which key is signing (supports rotation)
      X-ORC-Timestamp     — unix seconds (string); skew window ±300 s
      X-ORC-Nonce         — random per-request token (replay protection)
      X-ORC-Signature     — base64 Ed25519 signature over the canonical string

    On success returns (ConnectorKey, None) as the principal.
    Returns None when the identifying headers are absent (fall through to legacy).
    Raises AuthenticationFailed for present-but-invalid headers.
    """

    def authenticate(self, request):
        connector_id = request.META.get("HTTP_X_ORC_CONNECTOR_ID", "")
        signature_b64 = request.META.get("HTTP_X_ORC_SIGNATURE", "")

        # Both must be present for this authenticator to claim the request.
        if not connector_id or not signature_b64:
            return None

        key_id = request.META.get("HTTP_X_ORC_KEY_ID", "")
        ts = request.META.get("HTTP_X_ORC_TIMESTAMP", "")
        nonce = request.META.get("HTTP_X_ORC_NONCE", "")

        if not key_id or not ts or not nonce:
            raise AuthenticationFailed("Missing X-ORC-Key-Id, X-ORC-Timestamp, or X-ORC-Nonce")

        # Timestamp skew check — prevent replay of stale captures.
        try:
            ts_int = int(ts)
        except ValueError:
            raise AuthenticationFailed("X-ORC-Timestamp must be an integer") from None
        if abs(time.time() - ts_int) > 300:
            raise AuthenticationFailed("X-ORC-Timestamp outside ±300 s window")

        # Nonce replay check — each nonce is accepted at most once per 300 s window.
        cache_key = f"connsig:{connector_id}:{nonce}"
        if not cache.add(cache_key, 1, timeout=300):
            raise AuthenticationFailed("Nonce already used (replay detected)")

        # Key lookup — only active (non-revoked) keys are valid.
        try:
            key = ConnectorKey.objects.get(connector_id=connector_id, key_id=key_id, revoked_at=None)
        except ConnectorKey.DoesNotExist:
            raise AuthenticationFailed("Unknown or revoked connector key") from None

        # Signature verification against the canonical string.
        ok = verify_signature(
            key.public_key,
            method=request.method,
            path=request.path,
            body=request.body,
            ts=ts,
            nonce=nonce,
            signature_b64=signature_b64,
        )
        if not ok:
            raise AuthenticationFailed("Invalid signature")

        key.record_use()
        return (key, None)

    def authenticate_header(self, request):
        # Returning a non-empty string causes DRF to emit 401 instead of 403.
        return "Signature"


class ConnectorBearerAuthentication(BaseAuthentication):
    """No-op authenticator that only advertises the Bearer challenge.

    The actual token check lives in HasConnectorToken. Registering an
    authenticator with an `authenticate_header` makes DRF return 401 (not 403)
    when the permission denies an unauthenticated request.

    LEGACY: superseded by ConnectorSignatureAuthentication (UC0).
    Kept for backward compatibility with connectors that still use the shared
    ORC_CONNECTOR_TOKEN. Will be removed once all connectors migrate to Ed25519.
    """

    def authenticate(self, request):
        return None

    def authenticate_header(self, request):
        return "Bearer"


class HasConnectorToken(BearerTokenPermission):
    """Bearer token gate for the connector bridge endpoints.

    Accepts requests that are EITHER:
    1. Authenticated via ConnectorSignatureAuthentication (Ed25519, UC0), OR
    2. Carrying a valid ORC_CONNECTOR_TOKEN Bearer header (legacy, deprecated).

    Rejects with 503 if ORC_CONNECTOR_TOKEN is not configured AND the request
    is not signature-authenticated.
    """

    settings_attr = "ORC_CONNECTOR_TOKEN"
    unconfigured_flag = "_connector_token_unconfigured"

    def has_permission(self, request, view) -> bool:
        # If the request is already signature-authenticated, allow it through.
        if isinstance(request.successful_authenticator, ConnectorSignatureAuthentication):
            return True

        # Legacy shared-token path (DEPRECATED — migrate to Ed25519).
        return super().has_permission(request, view)
