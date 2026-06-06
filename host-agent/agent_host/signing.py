"""
signing.py — HMAC-SHA256 WebSocket authentication signature.

The formula must match backend apps/hostlink/security.sign exactly:

    key     = token (UTF-8 bytes)
    message = f"{host_id}:{ts}:{nonce}" (UTF-8 bytes)
    digest  = HMAC-SHA256 hexdigest

This module is intentionally minimal — no dependencies beyond the stdlib.
"""

from __future__ import annotations

import hashlib
import hmac


def sign(token: str, host_id: str, ts: int | str, nonce: str) -> str:
    """Return the HMAC-SHA256 hexdigest for a WebSocket auth handshake.

    Parameters
    ----------
    token:
        The per-host bearer token received at enroll time.
    host_id:
        The host's UUID string as returned by the enroll endpoint.
    ts:
        Unix timestamp (integer seconds).  Converted to str before hashing.
    nonce:
        A random one-time string (e.g. uuid4 hex, no hyphens).

    Returns
    -------
    str
        Lowercase hex digest string (64 characters for SHA-256).
    """
    message = f"{host_id}:{ts}:{nonce}".encode()
    key = token.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()
