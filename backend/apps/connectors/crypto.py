"""Ed25519 request-signing primitives for connector identity (UC0).

This is the security core: a connector proves identity by signing each request
with its private key; the backend verifies against the registered public key.
This replaces the single shared bearer token (one leaked token impersonated
every connector). Keep this module tiny and auditable.

Canonical signing string (exact, shared verbatim by every client):

    method.upper() + "\n" + path + "\n" + sha256_hex(body) + "\n" + ts + "\n" + nonce

- method: HTTP method, uppercased.
- path:   request path only, no query string (e.g. "/api/connectors/ask").
- body:   raw request body bytes (sha256 hex of b"" when empty).
- ts:     unix seconds (string).
- nonce:  random token, unique per request (replay-protected by the caller).

signature = base64( ed25519_sign(private_key, canonical_string.encode("utf-8")) )
public key + signature are base64 (standard, not url-safe).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def body_sha256_hex(body: bytes | None) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def canonical_string(method: str, path: str, body_sha256: str, ts: str, nonce: str) -> str:
    return "\n".join([method.upper(), path, body_sha256, str(ts), nonce])


def verify_signature(
    public_key_b64: str,
    *,
    method: str,
    path: str,
    body: bytes | None,
    ts: str,
    nonce: str,
    signature_b64: str,
) -> bool:
    """Verify an Ed25519 request signature. Returns True only on a valid match.

    Any malformed input (bad base64, wrong key length, bad signature) returns
    False — never raises — so callers can treat it as a flat auth failure.
    """
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        message = canonical_string(method, path, body_sha256_hex(body), ts, nonce).encode("utf-8")
        pub.verify(base64.b64decode(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
