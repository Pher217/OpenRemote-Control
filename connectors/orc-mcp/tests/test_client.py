"""Unit tests for OrcBackendClient using httpx.MockTransport (no network, no mcp)."""

import json

import httpx

from orc_mcp.client import OrcBackendClient


def _make(handler, **kw):
    # _signing_identity=None: suppress filesystem load so bearer fallback is exercised.
    return OrcBackendClient(
        base_url="http://test",
        token="tok",
        connector_id="c1",
        tool="claude",
        poll_interval=0,
        transport=httpx.MockTransport(handler),
        _signing_identity=None,
        **kw,
    )


def test_notify_posts_with_bearer_and_identity():
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"ok": True})

    assert _make(handler).notify("hello") is True
    assert seen["auth"] == "Bearer tok"
    assert seen["path"] == "/api/connectors/notify"
    assert seen["body"]["connector_id"] == "c1"
    assert seen["body"]["tool"] == "claude"


def test_notify_returns_false_on_error():
    def handler(req):
        return httpx.Response(500)

    assert _make(handler).notify("hello") is False


def test_start_remote_control_posts_and_returns_name():
    seen = {}

    def handler(req):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"ok": True, "thread_id": "t1", "name": "Hotfix"})

    assert _make(handler).start_remote_control("Hotfix") == "Hotfix"
    assert seen["path"] == "/api/connectors/start"
    assert seen["body"]["name"] == "Hotfix"
    assert seen["body"]["connector_id"] == "c1"


def test_start_remote_control_sends_session_binding():
    """claude_session_id + workspace_root are forwarded so the chat drives THIS session."""
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"ok": True, "thread_id": "t1", "name": "s"})

    _make(handler).start_remote_control(
        "s", claude_session_id="sid-123", workspace_root="/tmp/proj"
    )
    assert seen["body"]["claude_session_id"] == "sid-123"
    assert seen["body"]["workspace_root"] == "/tmp/proj"


def test_start_remote_control_omits_binding_when_absent():
    """No session id → no claude_session_id key (standalone session, unchanged)."""
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"ok": True, "thread_id": "t1", "name": "s"})

    _make(handler).start_remote_control("s")
    assert "claude_session_id" not in seen["body"]


def test_start_remote_control_sends_entrypoint_when_present():
    """entrypoint is forwarded so the backend knows this is a VSCode-extension session."""
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"ok": True, "thread_id": "t1", "name": "s"})

    _make(handler).start_remote_control("s", entrypoint="claude-vscode")
    assert seen["body"]["entrypoint"] == "claude-vscode"


def test_start_remote_control_omits_entrypoint_when_absent():
    """No entrypoint → no entrypoint key (normal CLI/headless session, unchanged)."""
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(201, json={"ok": True, "thread_id": "t1", "name": "s"})

    _make(handler).start_remote_control("s")
    assert "entrypoint" not in seen["body"]


def test_start_remote_control_returns_sentinel_on_error():
    def handler(req):
        return httpx.Response(500)

    assert _make(handler).start_remote_control("x") == "[connector error]"


def test_ask_posts_then_polls_until_answered():
    calls = {"result": 0}

    def handler(req):
        if req.url.path == "/api/connectors/ask":
            return httpx.Response(201, json={"nonce": "N1", "status": "pending"})
        calls["result"] += 1
        if calls["result"] == 1:
            return httpx.Response(200, json={"status": "pending"})
        return httpx.Response(200, json={"status": "answered", "answer": "main"})

    assert _make(handler).ask("Which branch?", ["main", "dev"]) == "main"
    assert calls["result"] == 2


def test_approve_maps_decision():
    def handler(req):
        if req.url.path == "/api/connectors/approve":
            return httpx.Response(201, json={"nonce": "N2", "status": "pending"})
        return httpx.Response(200, json={"status": "answered", "decision": "allow"})

    assert _make(handler).approve("deploy", "prod") == "allow"


def test_ask_timeout_returns_sentinel():
    def handler(req):
        if req.url.path.endswith("/ask"):
            return httpx.Response(201, json={"nonce": "N", "status": "pending"})
        return httpx.Response(200, json={"status": "pending"})

    assert _make(handler, overall_timeout=0).ask("q") == "[no answer: timeout]"


def test_approve_fail_closed_on_timeout():
    def handler(req):
        if req.url.path.endswith("/approve"):
            return httpx.Response(201, json={"nonce": "N", "status": "pending"})
        return httpx.Response(200, json={"status": "pending"})

    assert _make(handler, overall_timeout=0).approve("rm -rf") == "deny"


def test_ask_expired_returns_sentinel():
    def handler(req):
        if req.url.path.endswith("/ask"):
            return httpx.Response(201, json={"nonce": "N", "status": "pending"})
        return httpx.Response(200, json={"status": "expired"})

    assert _make(handler).ask("q") == "[expired]"
