"""Tests for orc_mcp.pair and OrcBackendClient auth-header selection."""

from __future__ import annotations

import json
import os
import pathlib

import httpx
import pytest

from orc_mcp.client import OrcBackendClient
from orc_mcp.pair import pair
from orc_mcp.signing import generate_keypair, load_or_create_identity, save_identity


# ---------------------------------------------------------------------------
# pair() tests — uses httpx.MockTransport + tmp HOME
# ---------------------------------------------------------------------------


def _mock_claim_transport(connector_id: str = "conn-abc", key_id: str = "key-xyz"):
    """Return a MockTransport that simulates the backend /pair/claim endpoint."""

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/connectors/pair/claim"
        body = json.loads(req.content)
        assert "code" in body
        assert "public_key" in body
        return httpx.Response(200, json={"connector_id": connector_id, "key_id": key_id})

    return httpx.MockTransport(handler)


def test_pair_posts_code_and_public_key(tmp_path, monkeypatch):
    """GIVEN a valid pairing code WHEN pair() is called THEN it POSTs code + public_key."""
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen["code"] = body.get("code")
        seen["public_key"] = body.get("public_key")
        seen["tool"] = body.get("tool")
        seen["label"] = body.get("label")
        return httpx.Response(200, json={"connector_id": "C1", "key_id": "K1"})

    pair("TOKEN-123", "http://test", _transport=httpx.MockTransport(handler))

    assert seen["code"] == "TOKEN-123"
    assert seen["public_key"] is not None
    import base64
    raw = base64.b64decode(seen["public_key"])
    assert len(raw) == 32  # raw Ed25519 public key
    assert seen["label"] is not None  # hostname


def test_pair_returns_connector_and_key_ids(tmp_path, monkeypatch):
    """GIVEN a 200 response WHEN pair() is called THEN it returns connector_id and key_id."""
    monkeypatch.setenv("HOME", str(tmp_path))
    result = pair("CODE", "http://test", _transport=_mock_claim_transport("conn-abc", "key-xyz"))
    assert result == {"connector_id": "conn-abc", "key_id": "key-xyz"}


def test_pair_saves_identity_to_config_dir(tmp_path, monkeypatch):
    """GIVEN a successful pair WHEN done THEN the key file and connector.json are written."""
    monkeypatch.setenv("HOME", str(tmp_path))
    pair("CODE", "http://test", _transport=_mock_claim_transport())

    config_dir = tmp_path / ".config" / "openremote-control"
    assert (config_dir / "connector_key").exists()
    assert (config_dir / "connector.json").exists()

    meta = json.loads((config_dir / "connector.json").read_text())
    assert meta["connector_id"] == "conn-abc"
    assert meta["key_id"] == "key-xyz"
    assert meta["backend_url"] == "http://test"


def test_pair_key_file_is_mode_0600(tmp_path, monkeypatch):
    """GIVEN a saved identity THEN the key file has mode 0600."""
    monkeypatch.setenv("HOME", str(tmp_path))
    pair("CODE", "http://test", _transport=_mock_claim_transport())
    key_file = tmp_path / ".config" / "openremote-control" / "connector_key"
    mode = oct(key_file.stat().st_mode)[-4:]
    assert mode == "0600"


def test_pair_raises_on_non_200(tmp_path, monkeypatch):
    """GIVEN a backend error response WHEN pair() is called THEN RuntimeError is raised."""
    monkeypatch.setenv("HOME", str(tmp_path))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    with pytest.raises(RuntimeError, match="403"):
        pair("BAD-CODE", "http://test", _transport=httpx.MockTransport(handler))


def test_pair_load_or_create_identity_after_save(tmp_path, monkeypatch):
    """GIVEN a saved identity WHEN load_or_create_identity is called THEN it returns priv + meta."""
    monkeypatch.setenv("HOME", str(tmp_path))
    pair("CODE", "http://test", _transport=_mock_claim_transport("C2", "K2"))

    # Now reload from the same tmp home
    result = load_or_create_identity()
    assert result is not None
    priv, meta = result
    assert meta["connector_id"] == "C2"
    assert meta["key_id"] == "K2"


# ---------------------------------------------------------------------------
# OrcBackendClient auth header tests
# ---------------------------------------------------------------------------


def _make_client(handler, *, signing_identity=None, token="", **kw):
    """Helper: build a client with MockTransport and controlled identity."""
    return OrcBackendClient(
        base_url="http://test",
        token=token,
        connector_id="c1",
        tool="claude",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
        _signing_identity=signing_identity,
        **kw,
    )


def test_client_uses_signing_headers_when_identity_present():
    """GIVEN a loaded signing identity WHEN a request is made THEN X-ORC-Signature is present."""
    seen = {}
    priv = generate_keypair()
    meta = {"connector_id": "conn-id", "key_id": "key-id", "backend_url": "http://test"}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.headers))
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler, signing_identity=(priv, meta))
    client.notify("hello")

    assert "x-orc-signature" in seen
    assert "x-orc-connector-id" in seen
    assert "x-orc-key-id" in seen
    assert "x-orc-timestamp" in seen
    assert "x-orc-nonce" in seen
    assert "authorization" not in seen


def test_client_uses_bearer_when_no_identity_but_token_set():
    """GIVEN no signing identity but a bearer token WHEN a request is made THEN Bearer header is used."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler, signing_identity=None, token="secret-token")
    client.notify("hello")

    assert seen["auth"] == "Bearer secret-token"
    assert "x-orc-signature" not in seen


def test_client_signing_headers_present_on_ask_post():
    """GIVEN a signing identity WHEN ask() POSTs THEN X-ORC-Signature is in the POST."""
    calls = []
    priv = generate_keypair()
    meta = {"connector_id": "c", "key_id": "k", "backend_url": "http://test"}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append({"path": req.url.path, "sig": req.headers.get("x-orc-signature")})
        if req.url.path == "/api/connectors/ask":
            return httpx.Response(201, json={"nonce": "N1", "status": "pending"})
        return httpx.Response(200, json={"status": "answered", "answer": "yes"})

    client = _make_client(handler, signing_identity=(priv, meta))
    client.ask("Question?")

    # Both the POST and the GET poll should carry the signature
    for call in calls:
        assert call["sig"] is not None, f"Missing sig on {call['path']}"


def test_client_signing_headers_present_on_get_poll():
    """GIVEN a signing identity WHEN the GET poll runs THEN X-ORC-Signature is in the GET."""
    seen_get = {}
    priv = generate_keypair()
    meta = {"connector_id": "c", "key_id": "k", "backend_url": "http://test"}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            seen_get.update(dict(req.headers))
            return httpx.Response(200, json={"status": "answered", "answer": "ok"})
        return httpx.Response(201, json={"nonce": "N", "status": "pending"})

    client = _make_client(handler, signing_identity=(priv, meta))
    client.ask("Q?")

    assert "x-orc-signature" in seen_get


def test_client_no_auth_header_when_no_identity_and_no_token():
    """GIVEN neither identity nor token WHEN a request is made THEN no auth header is sent."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        seen["sig"] = req.headers.get("x-orc-signature")
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler, signing_identity=None, token="")
    client.notify("hi")

    assert seen["auth"] is None
    assert seen["sig"] is None


def test_legacy_bearer_test_still_passes():
    """Regression: original test_notify_posts_with_bearer_and_identity still passes with new code."""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    client = _make_client(handler, signing_identity=None, token="tok")
    result = client.notify("hello")
    assert result is True
    assert seen["auth"] == "Bearer tok"
    assert seen["path"] == "/api/connectors/notify"
    assert seen["body"]["connector_id"] == "c1"
