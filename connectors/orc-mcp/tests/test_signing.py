"""Tests for orc_mcp.signing — pure unit, no network, no filesystem writes."""

from __future__ import annotations

import base64
import hashlib
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from orc_mcp.signing import generate_keypair, public_key_b64, sign_headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify(pub: Ed25519PublicKey, method: str, path: str, body: bytes, ts: str, nonce: str, sig_b64: str) -> None:
    """Reconstruct the canonical message and verify the signature — matches backend logic."""
    sha = hashlib.sha256(body).hexdigest()
    msg = "\n".join([method.upper(), path, sha, ts, nonce]).encode("utf-8")
    sig = base64.b64decode(sig_b64)
    # Raises InvalidSignature on mismatch (cryptography library)
    pub.verify(sig, msg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_generate_keypair_produces_32_byte_public_key():
    """GIVEN a fresh keypair WHEN we export the raw public key THEN it is 32 bytes."""
    priv = generate_keypair()
    raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    assert len(raw) == 32


def test_public_key_b64_is_standard_base64():
    """GIVEN a keypair WHEN encoded THEN the result is valid standard (non-URL-safe) base64."""
    priv = generate_keypair()
    b64 = public_key_b64(priv)
    # Must decode without error and produce 32 bytes
    raw = base64.b64decode(b64)
    assert len(raw) == 32
    # Must not contain URL-safe chars
    assert "-" not in b64
    assert "_" not in b64


def test_sign_headers_contains_all_five_fields():
    """GIVEN a POST request WHEN sign_headers is called THEN all five X-ORC-* headers are present."""
    priv = generate_keypair()
    headers = sign_headers(priv, "conn-1", "key-1", "POST", "/api/connectors/ask", b'{"hello":"world"}')
    for field in ("X-ORC-Connector-Id", "X-ORC-Key-Id", "X-ORC-Timestamp", "X-ORC-Nonce", "X-ORC-Signature"):
        assert field in headers, f"Missing header: {field}"


def test_sign_headers_connector_and_key_ids_match():
    """GIVEN specific ids WHEN sign_headers is called THEN the headers carry those exact ids."""
    priv = generate_keypair()
    headers = sign_headers(priv, "my-connector", "my-key", "POST", "/api/connectors/notify", b"")
    assert headers["X-ORC-Connector-Id"] == "my-connector"
    assert headers["X-ORC-Key-Id"] == "my-key"


def test_sign_headers_timestamp_is_recent_integer():
    """GIVEN a signed request THEN the timestamp is a decimal integer within ±5 s of now."""
    priv = generate_keypair()
    headers = sign_headers(priv, "c", "k", "GET", "/api/connectors/result/N", b"")
    ts = int(headers["X-ORC-Timestamp"])
    assert abs(ts - int(time.time())) <= 5


def test_sign_headers_nonce_is_16_hex_chars():
    """GIVEN a signed request THEN the nonce is a 16-char lowercase hex string (secrets.token_hex(8))."""
    priv = generate_keypair()
    headers = sign_headers(priv, "c", "k", "POST", "/api/connectors/ask", b"body")
    nonce = headers["X-ORC-Nonce"]
    assert len(nonce) == 16
    int(nonce, 16)  # must be valid hex


def test_sign_headers_signature_verifies_post():
    """GIVEN a POST with a JSON body WHEN signed THEN the signature verifies over the exact canonical message."""
    priv = generate_keypair()
    body = b'{"question":"ok?","options":[]}'
    headers = sign_headers(priv, "c", "k", "POST", "/api/connectors/ask", body)
    _verify(
        priv.public_key(),
        "POST",
        "/api/connectors/ask",
        body,
        headers["X-ORC-Timestamp"],
        headers["X-ORC-Nonce"],
        headers["X-ORC-Signature"],
    )


def test_sign_headers_signature_verifies_get_empty_body():
    """GIVEN a GET poll (empty body) WHEN signed THEN signature verifies with b'' body."""
    priv = generate_keypair()
    headers = sign_headers(priv, "c", "k", "GET", "/api/connectors/result/NONCE123", b"")
    _verify(
        priv.public_key(),
        "GET",
        "/api/connectors/result/NONCE123",
        b"",
        headers["X-ORC-Timestamp"],
        headers["X-ORC-Nonce"],
        headers["X-ORC-Signature"],
    )


def test_sign_headers_ts_and_nonce_vary_across_calls():
    """GIVEN two sequential sign_headers calls THEN nonces differ (probabilistic)."""
    priv = generate_keypair()
    h1 = sign_headers(priv, "c", "k", "POST", "/p", b"")
    h2 = sign_headers(priv, "c", "k", "POST", "/p", b"")
    assert h1["X-ORC-Nonce"] != h2["X-ORC-Nonce"]


def test_sign_headers_wrong_body_fails_verification():
    """GIVEN a signature over body A WHEN verified against body B THEN it raises InvalidSignature."""
    from cryptography.exceptions import InvalidSignature

    priv = generate_keypair()
    body_a = b'{"action":"deploy"}'
    headers = sign_headers(priv, "c", "k", "POST", "/api/connectors/approve", body_a)
    body_b = b'{"action":"rm -rf"}'
    try:
        _verify(
            priv.public_key(),
            "POST",
            "/api/connectors/approve",
            body_b,
            headers["X-ORC-Timestamp"],
            headers["X-ORC-Nonce"],
            headers["X-ORC-Signature"],
        )
        assert False, "Should have raised InvalidSignature"
    except InvalidSignature:
        pass


def test_sign_headers_method_is_uppercased():
    """GIVEN method 'post' (lowercase) WHEN signed THEN canonical uses 'POST' — same as 'POST'."""
    priv = generate_keypair()
    body = b"body"
    h_lower = sign_headers(priv, "c", "k", "post", "/api/connectors/ask", body)
    h_upper = sign_headers(priv, "c", "k", "POST", "/api/connectors/ask", body)
    # Both must verify as POST (different ts/nonce, but method casing normalised)
    _verify(
        priv.public_key(),
        "POST",
        "/api/connectors/ask",
        body,
        h_lower["X-ORC-Timestamp"],
        h_lower["X-ORC-Nonce"],
        h_lower["X-ORC-Signature"],
    )
    _verify(
        priv.public_key(),
        "POST",
        "/api/connectors/ask",
        body,
        h_upper["X-ORC-Timestamp"],
        h_upper["X-ORC-Nonce"],
        h_upper["X-ORC-Signature"],
    )
