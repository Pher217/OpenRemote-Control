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

    async def test_host_heartbeat_echoes_ping_via_group(self, host, host_token):
        """
        GIVEN a connected daemon
        WHEN a host_heartbeat message is sent with a nonce
        THEN the consumer echoes a host_command ping back through the group path,
             which arrives as a websocket.send frame on the same connection.
        """
        import asyncio
        import json as _json

        qs = _qs(host, host_token)
        communicator = WebsocketCommunicator(
            _test_application, f"ws/hosts/{host.id}/?{qs}"
        )
        connected, _ = await communicator.connect()
        assert connected is True

        await communicator.send_json_to(
            {"type": "host_heartbeat", "nonce": "abc-123"}
        )
        # Allow the in-memory channel layer to dispatch group_send → host_command.
        await asyncio.sleep(0.1)

        response_text = await communicator.receive_from(timeout=1)
        response = _json.loads(response_text)
        assert response["type"] == "host_command"
        assert response["command"] == "ping"
        assert response["nonce"] == "abc-123"

        await communicator.disconnect()
