"""WebSocket consumer tests for HostDaemonConsumer.

Uses an inline URLRouter so the test does not depend on the global routing
table (config/routing.py) being updated yet.

Cache: the test settings use RedisCache (redis://localhost:6379/1). If Redis
is unavailable in CI, override CACHES to LocMemCache via pytest-django's
``settings`` fixture or ``@override_settings``.
"""

import time
import uuid

import pytest
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.urls import path

from apps.hostlink.consumers import HostDaemonConsumer
from apps.hostlink.models import HostToken
from apps.hostlink.security import sign
from apps.hosts.models import Host
from apps.threads.models import Message, Thread

ENROLL_SECRET = "test-enroll-secret"

# Inline application for testing — avoids touching config/routing.py.
_test_application = URLRouter(
    [path("ws/hosts/<uuid:host_id>/", HostDaemonConsumer.as_asgi())]
)

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


@pytest.fixture
def host(db):
    return Host.objects.create(
        slug="daemon-host",
        name="Daemon Host",
        os=Host.OsChoices.LINUX,
        capabilities={"hw_uuid": "hw-test-uuid"},
    )


@pytest.fixture
def host_token(host):
    _, raw = HostToken.issue(host)
    return raw


def _qs(host, raw_token):
    """Build a valid query string for connecting to HostDaemonConsumer."""
    ts = str(int(time.time()))
    nonce = f"n-{uuid.uuid4().hex}"
    sig = sign(raw_token, str(host.id), ts, nonce)
    return f"token={raw_token}&ts={ts}&nonce={nonce}&signature={sig}"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestHostDaemonConsumer:
    @pytest.fixture(autouse=True)
    def _override(self, settings):
        settings.ORC_ENROLL_SECRET = ENROLL_SECRET
        settings.CACHES = LOCMEM_CACHES

    async def test_valid_credentials_accepted(self, host, host_token):
        """
        GIVEN a valid token and correctly signed query string
        WHEN a WebSocket connect is attempted
        THEN the connection is accepted
        """
        qs = _qs(host, host_token)
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()

    async def test_bad_token_rejected(self, host, host_token):
        """
        GIVEN an incorrect token
        WHEN a WebSocket connect is attempted
        THEN the connection is rejected (connected is False)
        """
        ts = str(int(time.time()))
        nonce = "bad-nonce"
        bad_token = "totally-wrong-token"
        sig = sign(bad_token, str(host.id), ts, nonce)
        qs = f"token={bad_token}&ts={ts}&nonce={nonce}&signature={sig}"
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, code = await communicator.connect()
        assert connected is False

    async def test_tampered_signature_rejected(self, host, host_token):
        """
        GIVEN a valid token but a tampered signature
        WHEN a WebSocket connect is attempted
        THEN the connection is rejected
        """
        ts = str(int(time.time()))
        nonce = "tamper-nonce"
        bad_sig = "a" * 64
        qs = f"token={host_token}&ts={ts}&nonce={nonce}&signature={bad_sig}"
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is False

    async def test_nonce_replay_rejected(self, host, host_token):
        """
        GIVEN the same nonce is used twice
        WHEN the second WebSocket connect is attempted
        THEN it is rejected (nonce replay)
        """
        ts = str(int(time.time()))
        nonce = "replay-nonce"
        sig = sign(host_token, str(host.id), ts, nonce)
        qs = f"token={host_token}&ts={ts}&nonce={nonce}&signature={sig}"

        # First connection — accepted.
        c1 = WebsocketCommunicator(_test_application, f"ws/hosts/{host.id}/?{qs}")
        connected1, _ = await c1.connect()
        assert connected1 is True
        await c1.disconnect()

        # Second connection with same nonce — must be rejected.
        c2 = WebsocketCommunicator(_test_application, f"ws/hosts/{host.id}/?{qs}")
        connected2, _ = await c2.connect()
        assert connected2 is False

    async def test_session_event_creates_thread_with_host(self, host, host_token):
        """
        GIVEN a connected daemon
        WHEN a session.event message is sent
        THEN a Thread is created and its host FK equals the enrolled host
        """
        from channels.db import database_sync_to_async

        qs = _qs(host, host_token)
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to(
            {
                "type": "session.event",
                "data": {
                    "session_id": "sess-abc-001",
                    "jsonl_path": "/tmp/fake.jsonl",
                    "provider": "claude_code",
                    "role": "user",
                    "text": "hello from the daemon",
                },
            }
        )

        # Give the consumer a moment to persist.
        import asyncio
        await asyncio.sleep(0.2)

        @database_sync_to_async
        def _get_thread():
            return Thread.objects.filter(external_session_ref="sess-abc-001").first()

        thread = await _get_thread()
        assert thread is not None
        assert thread.host_id == host.id

        await communicator.disconnect()

    async def test_session_event_persists_message(self, host, host_token):
        """
        GIVEN a connected daemon
        WHEN a session.event with role and text is sent
        THEN a Message is persisted for that thread
        """
        from channels.db import database_sync_to_async

        qs = _qs(host, host_token)
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to(
            {
                "type": "session.event",
                "data": {
                    "session_id": "sess-abc-002",
                    "jsonl_path": "/tmp/fake2.jsonl",
                    "provider": "claude_code",
                    "role": "assistant",
                    "text": "world from the daemon",
                },
            }
        )

        import asyncio
        await asyncio.sleep(0.2)

        @database_sync_to_async
        def _get_messages():
            thread = Thread.objects.filter(external_session_ref="sess-abc-002").first()
            if thread is None:
                return []
            return list(Message.objects.filter(thread=thread).values_list("redacted_content", flat=True))

        msgs = await _get_messages()
        assert "world from the daemon" in msgs

        await communicator.disconnect()

    async def test_session_line_parsed_server_side(self, host, host_token):
        """
        GIVEN a connected daemon shipping a RAW claude_code JSONL line
        WHEN a session.line message is sent
        THEN the backend parses it and persists a Message on a host-stamped Thread
        """
        import asyncio
        import json as _json

        from channels.db import database_sync_to_async

        qs = _qs(host, host_token)
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is True

        raw = _json.dumps(
            {
                "type": "assistant",
                "uuid": "u-line-1",
                "sessionId": "sess-line-001",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "from a raw line"}],
                },
            }
        )
        await communicator.send_json_to(
            {
                "type": "session.line",
                "data": {
                    "provider": "claude_code",
                    "jsonl_path": "/tmp/sess-line-001.jsonl",
                    "raw": raw,
                },
            }
        )
        await asyncio.sleep(0.2)

        @database_sync_to_async
        def _fetch():
            t = Thread.objects.filter(external_session_ref="sess-line-001").first()
            if t is None:
                return (None, [])
            return (
                t.host_id,
                list(
                    Message.objects.filter(thread=t).values_list(
                        "redacted_content", flat=True
                    )
                ),
            )

        host_id, msgs = await _fetch()
        assert host_id == host.id
        assert "from a raw line" in msgs

        await communicator.disconnect()
