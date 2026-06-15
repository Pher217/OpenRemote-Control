import json as _json

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.threads.dispatch import _build_history, dispatch_text
from apps.threads.models import Message, Thread


class _FakeOllamaResponse:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeOllamaClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None):
        lines = [
            _json.dumps(
                {"message": {"role": "assistant", "content": "Hello"}, "done": False}
            ),
            _json.dumps(
                {"message": {"role": "assistant", "content": " there"}, "done": False}
            ),
            _json.dumps(
                {"message": {"role": "assistant", "content": ""}, "done": True}
            ),
        ]
        return _FakeOllamaResponse(lines)


@database_sync_to_async
def _make_thread():
    account = Account.objects.create(
        provider="ollama",
        label="ollama-local",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="dispatch-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={"model": "test-model"},
    )
    return Thread.objects.select_related("account").get(id=thread.id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dispatch_text_streams_and_persists(monkeypatch):
    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _FakeOllamaClient)
    thread = await _make_thread()

    events = []

    async def on_event(d):
        events.append(d)

    await dispatch_text(thread, "Say hello.", on_event=on_event)

    deltas = [
        e
        for e in events
        if e["type"] == "message_delta" and e.get("text", "").strip()
    ]
    assert deltas

    completes = [e for e in events if e["type"] == "message_complete"]
    assert len(completes) == 1
    assert completes[0]["text"] == "Hello there"

    @database_sync_to_async
    def _assistant_contents():
        return list(
            Message.objects.filter(thread=thread, role="assistant").values_list(
                "redacted_content", flat=True
            )
        )

    assert await _assistant_contents() == ["Hello there"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_dispatch_text_slash_stop_stops_thread():
    thread = await _make_thread()

    events = []

    async def on_event(d):
        events.append(d)

    await dispatch_text(thread, "/stop", on_event=on_event)

    results = [e for e in events if e["type"] == "slash_result"]
    assert len(results) == 1
    assert results[0]["ok"] is True

    @database_sync_to_async
    def _status():
        return Thread.objects.get(id=thread.id).status

    assert await _status() == Thread.StatusChoices.STOPPED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_build_history_limits_to_max_history_messages():
    thread = await _make_thread()

    @database_sync_to_async
    def _create_messages():
        for i in range(1, 206):
            Message.objects.create(
                thread=thread,
                role="user" if i % 2 else "assistant",
                redacted_content=f"message {i}",
                sequence=i,
            )

    await _create_messages()
    history = await _build_history(thread)

    assert len(history) == 200

    numbers = [int(item["content"].split()[1]) for item in history]
    assert numbers == sorted(numbers)
    assert 1 not in numbers
    assert 205 in numbers
