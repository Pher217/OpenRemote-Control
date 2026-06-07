"""
Tests for signing.py — HMAC-SHA256 WebSocket authentication signature.

Known-vector test: compute the expected digest independently with hmac/hashlib
inside the test and verify sign() returns the same value.
"""

from __future__ import annotations

import hashlib
import hmac

from agent_host.signing import sign

# ---------------------------------------------------------------------------
# Known-vector test
# ---------------------------------------------------------------------------

class TestSignKnownVector:
    """Verify sign() against an independently-computed reference value."""

    TOKEN = "test-token-abc123"
    HOST_ID = "550e8400-e29b-41d4-a716-446655440000"
    TS = 1717660800  # 2024-06-06T08:00:00Z
    NONCE = "deadbeefcafe1234"

    def _expected(self) -> str:
        """Independently compute the expected hexdigest."""
        message = f"{self.HOST_ID}:{self.TS}:{self.NONCE}".encode()
        key = self.TOKEN.encode("utf-8")
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def test_known_vector_matches(self):
        """
        GIVEN a fixed token, host_id, ts, and nonce
        WHEN sign() is called
        THEN it returns the same HMAC-SHA256 hexdigest as the reference computation.
        """
        result = sign(self.TOKEN, self.HOST_ID, self.TS, self.NONCE)
        expected = self._expected()
        assert result == expected

    def test_known_vector_is_64_hex_chars(self):
        """SHA-256 hexdigest is always 64 lowercase hex characters."""
        result = sign(self.TOKEN, self.HOST_ID, self.TS, self.NONCE)
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_known_vector_explicit_value(self):
        """
        Hardcoded expected value so any future change to the formula is caught.

        Computed with:
            hmac.new(b'test-token-abc123',
                     b'550e8400-e29b-41d4-a716-446655440000:1717660800:deadbeefcafe1234',
                     hashlib.sha256).hexdigest()
        """
        result = sign(self.TOKEN, self.HOST_ID, self.TS, self.NONCE)
        # Recompute here so the test is self-documenting.
        expected = hmac.new(
            b"test-token-abc123",
            b"550e8400-e29b-41d4-a716-446655440000:1717660800:deadbeefcafe1234",
            hashlib.sha256,
        ).hexdigest()
        assert result == expected


# ---------------------------------------------------------------------------
# Determinism test
# ---------------------------------------------------------------------------

class TestSignDeterminism:
    def test_same_inputs_produce_same_output(self):
        """
        GIVEN identical inputs
        WHEN sign() is called twice
        THEN both calls return the same digest.
        """
        a = sign("tok", "host-1", 1000, "nonce1")
        b = sign("tok", "host-1", 1000, "nonce1")
        assert a == b

    def test_different_ts_produces_different_output(self):
        a = sign("tok", "host-1", 1000, "nonce1")
        b = sign("tok", "host-1", 1001, "nonce1")
        assert a != b

    def test_different_nonce_produces_different_output(self):
        a = sign("tok", "host-1", 1000, "nonceA")
        b = sign("tok", "host-1", 1000, "nonceB")
        assert a != b

    def test_different_token_produces_different_output(self):
        a = sign("token-A", "host-1", 1000, "nonce1")
        b = sign("token-B", "host-1", 1000, "nonce1")
        assert a != b

    def test_different_host_id_produces_different_output(self):
        a = sign("tok", "host-1", 1000, "nonce1")
        b = sign("tok", "host-2", 1000, "nonce1")
        assert a != b

    def test_ts_as_string_same_as_int(self):
        """ts may be passed as int or str — the message is always f-string formatted."""
        a = sign("tok", "host-1", 1000, "nonce1")
        b = sign("tok", "host-1", "1000", "nonce1")
        assert a == b
