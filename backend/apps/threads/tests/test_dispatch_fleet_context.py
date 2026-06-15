"""Tests for the extra_system_context parameter on dispatch_text.

Coverage:
  - extra_system_context non-empty → adapter receives history whose first message
    is a system role entry with that content.
  - extra_system_context absent (None) → adapter receives no prepended system
    message (behaviour byte-for-byte unchanged).
  - The injected system message is NOT persisted to the DB (Message count is
    unaffected by the injection).
"""

from __future__ import annotations

import json as _json

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.threads.dispatch import dispatch_text
from apps.threads.models import Message, Thread

# ---------------------------------------------------------------------------
# Fake adapter infrastructure — captures the history passed to stream()
# ---------------------------------------------------------------------------


class _CapturingFakeOllamaResponse:
    """Async context manager that yields a single 'done' line."""

    def __init__(self):
        self._lines = [
            _json.dumps(
                {"message": {"role": "assistant", "content": "ok"}, "done": True}
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


class _CapturingFakeOllamaClient:
    """Records every `history` list passed to stream() in a shared capture list."""

    def __init__(self, captures, *args, **kwargs):
        self._captures = captures

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None):
        # The Ollama adapter serialises history inside the json payload
        # as json["messages"]; capture it here for assertions.
        if json:
            self._captures.append(json.get("messages", []))
        return _CapturingFakeOllamaResponse()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@database_sync_to_async
def _make_thread():
    account = Account.objects.create(
        provider="ollama",
        label="ollama-fleet-ctx-test",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="fleet-ctx-dispatch-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={"model": "test-model"},
    )
    return Thread.objects.select_related("account").get(id=thread.id)


@database_sync_to_async
def _message_count(thread):
    return Message.objects.filter(thread=thread).count()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_extra_system_context_prepended_to_history(monkeypatch):
    """
    GIVEN dispatch_text is called with extra_system_context="FLEET_INFO"
    WHEN the adapter's stream() is invoked
    THEN the first message in the history list has role 'system' and
         content 'FLEET_INFO'.
    """
    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingFakeOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)
    thread = await _make_thread()

    events = []

    async def on_event(d):
        events.append(d)

    await dispatch_text(thread, "hello", on_event=on_event, extra_system_context="FLEET_INFO")

    assert captures, "adapter.stream() was never called"
    messages = captures[0]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "FLEET_INFO"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_extra_system_context_leaves_history_unchanged(monkeypatch):
    """
    GIVEN dispatch_text is called WITHOUT extra_system_context
    WHEN the adapter's stream() is invoked
    THEN no system message is prepended (history starts with the user message).
    """
    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingFakeOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)
    thread = await _make_thread()

    events = []

    async def on_event(d):
        events.append(d)

    await dispatch_text(thread, "hello", on_event=on_event)

    assert captures, "adapter.stream() was never called"
    messages = captures[0]
    # The first message should NOT be a bare system injection — it should be user
    assert messages[0]["role"] == "user"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_extra_system_context_not_persisted_to_db(monkeypatch):
    """
    GIVEN dispatch_text is called with extra_system_context="EPHEMERAL"
    WHEN the call completes
    THEN the DB contains only the persisted user message and assistant reply —
         no additional Message row for the injected system context.
    """
    captures = []

    def _client_factory(*args, **kwargs):
        return _CapturingFakeOllamaClient(captures, *args, **kwargs)

    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _client_factory)
    thread = await _make_thread()

    before = await _message_count(thread)

    events = []

    async def on_event(d):
        events.append(d)

    await dispatch_text(thread, "hello", on_event=on_event, extra_system_context="EPHEMERAL")

    after = await _message_count(thread)
    # Expect exactly 2 new messages: user + assistant (no system row)
    assert after - before == 2
