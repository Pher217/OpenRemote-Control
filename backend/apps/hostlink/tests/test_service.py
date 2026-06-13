"""Tests for apps/hostlink/service.py — send_host_command and ping_host.

Strategy: use channels InMemoryChannelLayer (no Redis needed) so the test is
hermetic.  send_host_command uses async_to_sync internally (it's designed to
be called from synchronous code — Django signals, management commands, views).
We call it from synchronous test context, then receive the frame asynchronously.
"""

from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import override_settings


# Use the in-memory channel layer so no Redis is needed for this test.
INMEM_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


@pytest.fixture
def host(db):
    from apps.hosts.models import Host

    return Host.objects.create(
        slug="svc-test-host",
        name="Service Test Host",
        os=Host.OsChoices.LINUX,
        capabilities={"hw_uuid": "svc-hw-uuid"},
    )


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_host_command_delivers_to_group(host, settings):
    """
    GIVEN a host and an in-memory channel layer with a listener on host_{host.id}
    WHEN send_host_command(host, "ping") is called from synchronous context
    THEN the group receives a frame with type="host_command" and command="ping".
    """
    # Override at the settings level so get_channel_layer() sees it.
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_host_command

    channel_layer = get_channel_layer()
    assert channel_layer is not None

    group_name = f"host_{host.id}"

    # Register a channel in the target group (must happen in async context).
    channel_name = async_to_sync(channel_layer.new_channel)()
    async_to_sync(channel_layer.group_add)(group_name, channel_name)

    # Call the synchronous service function from this sync test body.
    send_host_command(host, "ping")

    # Receive and verify the frame.
    message = async_to_sync(channel_layer.receive)(channel_name)

    async_to_sync(channel_layer.group_discard)(group_name, channel_name)

    assert message["type"] == "host_command"
    assert message["command"] == "ping"


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_ping_host_sends_ping_command(host, settings):
    """
    GIVEN a host and an in-memory channel layer with a listener on host_{host.id}
    WHEN ping_host(host) is called
    THEN the group receives a frame with command="ping".
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import ping_host

    channel_layer = get_channel_layer()
    assert channel_layer is not None

    group_name = f"host_{host.id}"

    channel_name = async_to_sync(channel_layer.new_channel)()
    async_to_sync(channel_layer.group_add)(group_name, channel_name)

    ping_host(host)

    message = async_to_sync(channel_layer.receive)(channel_name)
    async_to_sync(channel_layer.group_discard)(group_name, channel_name)

    assert message["command"] == "ping"


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_host_command_includes_extra_payload(host, settings):
    """
    GIVEN a host and extra payload kwargs
    WHEN send_host_command(host, "pty.inject", keys="ls\\n") is called
    THEN the frame delivered to the group includes the extra keys.
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_host_command

    channel_layer = get_channel_layer()
    assert channel_layer is not None

    group_name = f"host_{host.id}"

    channel_name = async_to_sync(channel_layer.new_channel)()
    async_to_sync(channel_layer.group_add)(group_name, channel_name)

    send_host_command(host, "pty.inject", keys="ls\n")

    message = async_to_sync(channel_layer.receive)(channel_name)
    async_to_sync(channel_layer.group_discard)(group_name, channel_name)

    assert message["type"] == "host_command"
    assert message["command"] == "pty.inject"
    assert message["keys"] == "ls\n"
