"""
hostlink/service.py — Backend producer for downstream host commands.

send_host_command(host, command, **payload)
    Group-sends a host_command event to the connected daemon for *host* via
    the channel layer.  The HostDaemonConsumer's ``host_command`` handler
    (consumers.py:202) forwards the event as JSON over the open WebSocket.

ping_host(host)
    Convenience wrapper that sends a "ping" host_command.
"""

from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def send_host_command(host, command: str, **payload) -> None:
    """Send a command frame to the daemon connected for *host*.

    The frame is group-sent to ``host_{host.id}``, the group name the
    HostDaemonConsumer joins on connect (consumers.py:86).  The consumer's
    ``host_command`` handler (consumers.py:202) forwards it as JSON over the
    open WebSocket, where the daemon's receive loop (wsclient.py) picks it up.

    Uses ``async_to_sync`` following the same idiom as
    ``apps.threads.signals.thread_post_save_broadcast``.

    Parameters
    ----------
    host:
        A ``Host`` model instance.  Only its ``id`` is used.
    command:
        Command name, e.g. ``"ping"``.  The daemon dispatches on this value.
    **payload:
        Additional key/value pairs merged into the event frame.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    group_name = f"host_{host.id}"
    event = {"type": "host_command", "command": command, **payload}
    async_to_sync(channel_layer.group_send)(group_name, event)


def ping_host(host) -> None:
    """Send a ping host_command to the daemon connected for *host*.

    Parameters
    ----------
    host:
        A ``Host`` model instance.
    """
    send_host_command(host, "ping")
