import json as _json
import uuid

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from django.contrib.sessions.backends.db import SessionStore

from apps.accounts.models import Account
from apps.threads.models import Message, Thread
from config.asgi import application

# Origin only — NOT authentication.  Anonymous connections are rejected (4401).
HEADERS = [(b"origin", b"http://localhost")]


@database_sync_to_async
def _operator_session_key():
    """Create an operator user and a live session, returning the session key."""
    user = get_user_model().objects.create_user(username="operator", password="pw")
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.save()
    return session.session_key


async def _auth_headers():
    """Origin + a session cookie for an authenticated operator."""
    key = await _operator_session_key()
    return HEADERS + [(b"cookie", f"sessionid={key}".encode())]


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
            application, f"/ws/threads/{ollama_thread.id}/", headers=await _auth_headers()
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    async def test_anonymous_connection_rejected(self, ollama_thread):
        """
        GIVEN a valid thread id but no authenticated session
        WHEN a client opens the thread WebSocket
        THEN the connection is refused

        Knowing a thread UUID must not grant the ability to dispatch text into
        a live agent session.
        """
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{ollama_thread.id}/", headers=HEADERS
        )
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4401

    async def test_anonymous_cannot_dispatch_to_unknown_thread(self):
        """Anonymous is rejected on auth, before any thread lookup happens."""
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{uuid.uuid4()}/", headers=HEADERS
        )
        connected, code = await communicator.connect()
        assert connected is False
        assert code == 4401

    async def test_unknown_thread_rejected(self):
        communicator = WebsocketCommunicator(
            application, f"/ws/threads/{uuid.uuid4()}/", headers=await _auth_headers()
        )
        connected, _ = await communicator.connect()
        assert connected is False

    async def test_slash_stop_acks_and_stops(self, ollama_thread):
        communicator = WebsocketCommunicator(
            application,
            f"/ws/threads/{ollama_thread.id}/",
            headers=await _auth_headers(),
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
        application, f"/ws/threads/{ollama_thread.id}/", headers=await _auth_headers()
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
