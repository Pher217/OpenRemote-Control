"""Tests for fleet context injection in handle_update (non-slash branch).

Coverage:
  - handle_update (non-slash) → build_fleet_state is called and its
    render_digest output is forwarded to the adapter as the first system
    message in the history, confirming the fleet digest reaches the adapter.
"""

from __future__ import annotations

import json as _json

import pytest

from apps.telegram.service import handle_update


# ---------------------------------------------------------------------------
# Fake Ollama adapter that captures history messages
# ---------------------------------------------------------------------------


class _CapturingResponse:
    """Async context-manager response that emits one done line."""

    def __init__(self):
        self._lines = [
            _json.dumps(
                {"message": {"role": "assistant", "content": "sure"}, "done": True}
            )
        ]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _CapturingOllamaClient:
    """Records 'messages' from every stream() call into the shared list."""

    def __init__(self, captures, *args, **kwargs):
        self._captures = captures

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None):
        if json:
            self._captures.append(json.get("messages", []))
        return _CapturingResponse()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_update_passes_fleet_digest_as_system_context(monkeypatch, settings):
    """
    GIVEN a non-slash plain-text Telegram message from an allowlisted chat
    WHEN build_fleet_state returns a non-empty fleet snapshot
    THEN the adapter receives the render_digest output as the first system
         message in the history passed to stream().
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {55555}

    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)

    # Stub fleet to return a known single running session
    from datetime import timedelta

    fake_fleet = [
        {
            "thread_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "label": "my-project",
            "runtime_mode": "pty",
            "host": "local",
            "status": "running",
            "last_event_at": None,
            "age": timedelta(minutes=5),
            "needs_input": False,
        }
    ]

    monkeypatch.setattr(
        "apps.telegram.service.build_fleet_state",
        lambda: fake_fleet,
    )

    sent = []

    async def cap(cid, txt, **kwargs):
        sent.append((cid, txt))

    await handle_update(55555, "What are my sessions?", from_user_id=55555, send=cap)

    assert captures, "adapter.stream() was never called"
    messages = captures[0]

    # First message must be a system-role fleet digest
    assert messages[0]["role"] == "system"
    # The digest content contains the session label and render_digest header
    assert "my-project" in messages[0]["content"]
    assert "Fleet digest" in messages[0]["content"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_update_fleet_digest_present_when_no_sessions(monkeypatch, settings):
    """
    GIVEN a non-slash plain-text Telegram message from an allowlisted chat
    WHEN build_fleet_state returns an empty list (no active sessions)
    THEN the adapter still receives a system message containing
         'No active sessions.' from render_digest.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {55556}

    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)

    monkeypatch.setattr(
        "apps.telegram.service.build_fleet_state",
        lambda: [],
    )

    sent = []

    async def cap(cid, txt, **kwargs):
        sent.append((cid, txt))

    await handle_update(55556, "Anything going on?", from_user_id=55556, send=cap)

    assert captures, "adapter.stream() was never called"
    messages = captures[0]

    assert messages[0]["role"] == "system"
    assert "No active sessions" in messages[0]["content"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_update_no_fleet_digest_for_non_operator(monkeypatch, settings):
    """
    GIVEN a non-slash message in an allowlisted group/forum chat
    WHEN the SENDER (from_user_id) is NOT an allowlisted operator
    THEN the fleet digest is NOT built or injected — the fleet listing is
         operator-only (same read-boundary as /sessions), so a non-operator
         gets plain chat with no system fleet context.
    """
    # The chat is allowlisted (so general chat is reachable) but the sender is not.
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {-1009999999999}

    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)

    fleet_calls = []
    monkeypatch.setattr(
        "apps.telegram.service.build_fleet_state",
        lambda: fleet_calls.append(1) or [],
    )

    sent = []

    async def cap(cid, txt, **kwargs):
        sent.append((cid, txt))

    # chat_id allowlisted, but from_user_id 424242 is NOT in the allowlist.
    await handle_update(
        -1009999999999, "What are my sessions?", from_user_id=424242, send=cap
    )

    assert captures, "adapter.stream() was never called"
    messages = captures[0]

    # The fleet snapshot must never have been built for a non-operator.
    assert fleet_calls == []
    # No system message carrying the fleet digest reached the adapter.
    assert not any(
        m.get("role") == "system" and "active coding sessions" in m.get("content", "")
        for m in messages
    )
