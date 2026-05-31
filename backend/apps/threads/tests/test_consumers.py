import json as _json
import uuid

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from apps.accounts.models import Account
from apps.threads.models import Message, Thread
from config.asgi import application

HEADERS = [(b"origin", b"http://localhost")]


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


@pytest.fixture
def ollama_thread(db):
    account = Account.objects.create(
        provider="ollama",
        label="ollama-local",
        auth_type="none",
        credential_type="none",
    )
    return Thread.objects.create(
        name="ws-thread",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={"model": "test-model"},
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestThreadConsumer:
    async def test_connect_ok(self, ollama_thread):
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{ollama_thread.id}/", headers=HEADERS
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    async def test_unknown_thread_rejected(self):
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{uuid.uuid4()}/", headers=HEADERS
        )
        connected, _ = await communicator.connect()
        assert connected is False

    async def test_slash_stop_acks_and_stops(self, ollama_thread):
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{ollama_thread.id}/", headers=HEADERS
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.send_json_to({"text": "/stop"})
        response = await communicator.receive_json_from(timeout=5)
        assert response["type"] == "slash_result"
        assert response["ok"] is True
        await communicator.disconnect()

        @database_sync_to_async
        def _status():
            return Thread.objects.get(id=ollama_thread.id).status

        assert await _status() == Thread.StatusChoices.STOPPED


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_ollama_stream_persists_assistant_message(ollama_thread, monkeypatch):
    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _FakeOllamaClient)
    communicator = WebsocketCommunicator(
        application, f"/ws/threads/{ollama_thread.id}/", headers=HEADERS
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.send_json_to({"text": "Say hello in one short word."})

    deltas = []
    complete = None
    while True:
        response = await communicator.receive_json_from(timeout=5)
        if response["type"] == "message_delta" and response.get("text", "").strip():
            deltas.append(response["text"])
        elif response["type"] == "message_complete":
            complete = response
            break
    # streamed assistant delta arrived
    assert deltas
    assert "".join(deltas) == "Hello there"
    # message_complete carried the full text
    assert complete["text"] == "Hello there"
    await communicator.disconnect()

    @database_sync_to_async
    def _assistant_contents():
        return list(
            Message.objects.filter(
                thread=ollama_thread, role="assistant"
            ).values_list("redacted_content", flat=True)
        )

    contents = await _assistant_contents()
    # an assistant Message persisted with non-empty redacted_content
    assert contents == ["Hello there"]
