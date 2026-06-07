"""Tests for UC0 per-connector Ed25519 identity + pairing.

Key generation uses cryptography.hazmat directly — the same library crypto.py
relies on — so tests exercise the real signing path end-to-end.
"""

from __future__ import annotations

import base64
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from rest_framework.test import APIClient

from apps.connectors.crypto import body_sha256_hex, canonical_string
from apps.connectors.models import ConnectorKey, Pairing

BASE = "/api/connectors"
TOKEN = "test-connector-token-abc"
AUTH_LEGACY = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gen_keypair():
    """Return (private_key, public_key_b64) for an Ed25519 keypair."""
    private = Ed25519PrivateKey.generate()
    pub_bytes = private.public_key().public_bytes_raw()
    return private, base64.b64encode(pub_bytes).decode()


def _sign(private_key, *, method, path, body: bytes = b"", ts=None, nonce="testnonce"):
    """Return the base64 signature for a request."""
    if ts is None:
        ts = str(int(time.time()))
    sha = body_sha256_hex(body)
    msg = canonical_string(method, path, sha, ts, nonce).encode("utf-8")
    sig_bytes = private_key.sign(msg)
    return base64.b64encode(sig_bytes).decode(), ts


def _sig_headers(connector_id, key_id, private_key, *, method, path, body=b"", nonce=None):
    """Build the full dict of X-ORC-* headers for a request."""
    if nonce is None:
        import secrets
        nonce = secrets.token_hex(8)
    sig, ts = _sign(private_key, method=method, path=path, body=body, nonce=nonce)
    return {
        "HTTP_X_ORC_CONNECTOR_ID": connector_id,
        "HTTP_X_ORC_KEY_ID": key_id,
        "HTTP_X_ORC_TIMESTAMP": ts,
        "HTTP_X_ORC_NONCE": nonce,
        "HTTP_X_ORC_SIGNATURE": sig,
    }


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture(autouse=True)
def patch_telegram(monkeypatch):
    import apps.telegram.telegram_api as tg_api

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(tg_api, "send_message", _noop)


@pytest.fixture
def with_legacy_token(settings):
    settings.ORC_CONNECTOR_TOKEN = TOKEN
    settings.ORC_PROMPT_CHAT_ID = ""


@pytest.fixture
def connector_key(db):
    """A fresh keypair registered as a ConnectorKey in the DB."""
    private, pub_b64 = _gen_keypair()
    key = ConnectorKey.objects.create(
        connector_id="conn-test-001",
        key_id="key001",
        public_key=pub_b64,
        tool="cursor",
        label="test",
    )
    return key, private


# ---------------------------------------------------------------------------
# Signature authentication tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSignatureAuth:
    def test_valid_signature_passes(self, client, connector_key, settings):
        """
        GIVEN a registered ConnectorKey
        WHEN a request is signed with the matching private key
        THEN 200 is returned (not 401/403)
        """
        settings.ORC_PROMPT_CHAT_ID = ""
        key, private = connector_key
        path = f"{BASE}/notify"
        body = b'{"connector_id":"conn-test-001","tool":"cursor","message":"hi"}'
        hdrs = _sig_headers(key.connector_id, key.key_id, private, method="POST", path=path, body=body)
        resp = client.post(
            path,
            data=body,
            content_type="application/json",
            **hdrs,
        )
        assert resp.status_code == 200

    def test_wrong_signature_returns_401(self, client, connector_key, settings):
        """
        GIVEN a registered ConnectorKey
        WHEN a request carries a bad (random) signature
        THEN 401 is returned
        """
        settings.ORC_PROMPT_CHAT_ID = ""
        key, private = connector_key
        bad_sig = base64.b64encode(b"\xff" * 64).decode()
        ts = str(int(time.time()))
        resp = client.post(
            f"{BASE}/notify",
            data=b'{"connector_id":"conn-test-001","tool":"cursor","message":"hi"}',
            content_type="application/json",
            HTTP_X_ORC_CONNECTOR_ID=key.connector_id,
            HTTP_X_ORC_KEY_ID=key.key_id,
            HTTP_X_ORC_TIMESTAMP=ts,
            HTTP_X_ORC_NONCE="unique-nonce-bad",
            HTTP_X_ORC_SIGNATURE=bad_sig,
        )
        assert resp.status_code == 401

    def test_missing_signature_headers_falls_to_legacy_check(self, client, settings):
        """
        GIVEN no X-ORC-* headers at all (legacy client)
        WHEN no ORC_CONNECTOR_TOKEN is configured either
        THEN 503 (unconfigured) is returned — proving legacy path is exercised
        """
        settings.ORC_CONNECTOR_TOKEN = ""
        settings.ORC_PROMPT_CHAT_ID = ""
        resp = client.post(
            f"{BASE}/notify",
            {"connector_id": "c1", "tool": "cursor", "message": "hi"},
            format="json",
        )
        assert resp.status_code == 503

    def test_expired_timestamp_returns_401(self, client, connector_key, settings):
        """
        GIVEN a signed request with a timestamp >300 s in the past
        WHEN the request arrives
        THEN 401 is returned (timestamp skew)
        """
        settings.ORC_PROMPT_CHAT_ID = ""
        key, private = connector_key
        old_ts = str(int(time.time()) - 400)
        sig, _ = _sign(private, method="POST", path=f"{BASE}/notify", ts=old_ts, nonce="stale-nonce")
        resp = client.post(
            f"{BASE}/notify",
            data=b'{"connector_id":"conn-test-001","tool":"cursor","message":"hi"}',
            content_type="application/json",
            HTTP_X_ORC_CONNECTOR_ID=key.connector_id,
            HTTP_X_ORC_KEY_ID=key.key_id,
            HTTP_X_ORC_TIMESTAMP=old_ts,
            HTTP_X_ORC_NONCE="stale-nonce",
            HTTP_X_ORC_SIGNATURE=sig,
        )
        assert resp.status_code == 401

    def test_replayed_nonce_returns_401(self, client, connector_key, settings):
        """
        GIVEN a valid signed request that has already been accepted
        WHEN the identical nonce is replayed
        THEN 401 is returned (replay detection)
        """
        import secrets as _secrets

        settings.ORC_PROMPT_CHAT_ID = ""
        key, private = connector_key
        path = f"{BASE}/notify"
        body = b'{"connector_id":"conn-test-001","tool":"cursor","message":"hi"}'
        # Unique per test run so Redis state from previous runs does not interfere.
        nonce = f"replay-nonce-{_secrets.token_hex(8)}"
        hdrs = _sig_headers(key.connector_id, key.key_id, private, method="POST", path=path, body=body, nonce=nonce)

        # First request must succeed.
        r1 = client.post(path, data=body, content_type="application/json", **hdrs)
        assert r1.status_code == 200

        # Second request with the same nonce must be rejected.
        r2 = client.post(path, data=body, content_type="application/json", **hdrs)
        assert r2.status_code == 401

    def test_revoked_key_returns_401(self, client, connector_key, settings):
        """
        GIVEN a ConnectorKey that has been revoked
        WHEN a correctly signed request arrives
        THEN 401 is returned
        """
        from django.utils import timezone

        settings.ORC_PROMPT_CHAT_ID = ""
        key, private = connector_key
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])

        path = f"{BASE}/notify"
        body = b'{"connector_id":"conn-test-001","tool":"cursor","message":"hi"}'
        hdrs = _sig_headers(key.connector_id, key.key_id, private, method="POST", path=path, body=body)
        resp = client.post(path, data=body, content_type="application/json", **hdrs)
        assert resp.status_code == 401

    def test_legacy_token_still_works(self, client, with_legacy_token):
        """
        GIVEN a request using the legacy ORC_CONNECTOR_TOKEN Bearer header
        WHEN no X-ORC-* headers are present
        THEN 200 is returned (backward compatibility)
        """
        resp = client.post(
            f"{BASE}/notify",
            {"connector_id": "c-legacy", "tool": "cursor", "message": "legacy hi"},
            format="json",
            **AUTH_LEGACY,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# pair/claim tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestPairClaim:
    def _make_pairing(self, ttl=900, tool="cursor", label="test"):
        from datetime import timedelta

        from django.utils import timezone

        return Pairing.objects.create(
            tool=tool,
            label=label,
            expires_at=timezone.now() + timedelta(seconds=ttl),
        )

    def test_valid_claim_creates_key_and_returns_ids(self, client):
        """
        GIVEN a valid unexpired Pairing
        WHEN POST /pair/claim with code + public_key
        THEN 200 {connector_id, key_id} and a ConnectorKey row exist
        """
        _, pub_b64 = _gen_keypair()
        pairing = self._make_pairing()

        resp = client.post(
            f"{BASE}/pair/claim",
            {"code": pairing.code, "tool": "cursor", "public_key": pub_b64, "label": "laptop"},
            format="json",
        )
        assert resp.status_code == 200
        assert "connector_id" in resp.data
        assert "key_id" in resp.data

        ck = ConnectorKey.objects.get(connector_id=resp.data["connector_id"])
        assert ck.public_key == pub_b64
        assert ck.tool == "cursor"

        pairing.refresh_from_db()
        assert pairing.claimed_at is not None
        assert pairing.connector_id == resp.data["connector_id"]

    def test_expired_code_returns_410(self, client):
        """
        GIVEN a Pairing whose expires_at is in the past
        WHEN POST /pair/claim
        THEN 410 is returned
        """
        from datetime import timedelta

        from django.utils import timezone

        _, pub_b64 = _gen_keypair()
        pairing = Pairing.objects.create(
            tool="cursor",
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        resp = client.post(
            f"{BASE}/pair/claim",
            {"code": pairing.code, "public_key": pub_b64},
            format="json",
        )
        assert resp.status_code == 410

    def test_already_used_code_returns_410(self, client):
        """
        GIVEN a Pairing that has already been claimed
        WHEN POST /pair/claim is called again with the same code
        THEN 410 is returned
        """
        from django.utils import timezone

        _, pub_b64 = _gen_keypair()
        pairing = self._make_pairing()
        pairing.claimed_at = timezone.now()
        pairing.save(update_fields=["claimed_at"])

        resp = client.post(
            f"{BASE}/pair/claim",
            {"code": pairing.code, "public_key": pub_b64},
            format="json",
        )
        assert resp.status_code == 410

    def test_unknown_code_returns_404(self, client):
        """
        GIVEN a code that does not exist in the DB
        WHEN POST /pair/claim
        THEN 404 is returned
        """
        _, pub_b64 = _gen_keypair()
        resp = client.post(
            f"{BASE}/pair/claim",
            {"code": "does-not-exist", "public_key": pub_b64},
            format="json",
        )
        assert resp.status_code == 404

    def test_claimed_key_can_sign_requests(self, client, settings):
        """
        GIVEN a pairing that was just claimed
        WHEN a signed request is made with the registered key
        THEN 200 is returned (end-to-end flow)
        """
        settings.ORC_PROMPT_CHAT_ID = ""
        private, pub_b64 = _gen_keypair()
        pairing = self._make_pairing()

        claim_resp = client.post(
            f"{BASE}/pair/claim",
            {"code": pairing.code, "tool": "cursor", "public_key": pub_b64},
            format="json",
        )
        assert claim_resp.status_code == 200
        connector_id = claim_resp.data["connector_id"]
        key_id = claim_resp.data["key_id"]

        path = f"{BASE}/notify"
        body = f'{{"connector_id":"{connector_id}","tool":"cursor","message":"hello"}}'.encode()
        hdrs = _sig_headers(connector_id, key_id, private, method="POST", path=path, body=body)
        resp = client.post(path, data=body, content_type="application/json", **hdrs)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# QR utility tests
# ---------------------------------------------------------------------------


def test_terminal_qr_produces_non_empty_string():
    """
    GIVEN a pairing payload string
    WHEN terminal_qr is called
    THEN a non-empty string is returned
    """
    from apps.connectors.qr import terminal_qr

    result = terminal_qr("orc-pair://orc.example.com/abc123")
    assert isinstance(result, str)
    assert len(result) > 10


def test_png_bytes_produces_png():
    """
    GIVEN a pairing payload string
    WHEN png_bytes is called
    THEN bytes starting with the PNG magic header are returned
    """
    from apps.connectors.qr import png_bytes

    result = png_bytes("orc-pair://orc.example.com/abc123")
    assert isinstance(result, bytes)
    assert len(result) > 100
    assert result[:4] == b"\x89PNG"


def test_pairing_payload_with_backend_url():
    """
    GIVEN a code and a backend URL
    WHEN pairing_payload is called
    THEN the result is orc-pair://<host>/<code>
    """
    from apps.connectors.qr import pairing_payload

    result = pairing_payload("mycode", "https://orc.example.com")
    assert result == "orc-pair://orc.example.com/mycode"


def test_pairing_payload_without_backend_url():
    """
    GIVEN a code and an empty backend URL
    WHEN pairing_payload is called
    THEN the result is just the code
    """
    from apps.connectors.qr import pairing_payload

    result = pairing_payload("mycode", "")
    assert result == "mycode"
