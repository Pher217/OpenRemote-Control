"""
hostlink/service.py — Backend producer for downstream host commands.

send_host_command(host, command, **payload)
    Group-sends a host_command event to the connected daemon for *host* via
    the channel layer.  The HostDaemonConsumer's ``host_command`` handler
    (consumers.py:202) forwards the event as JSON over the open WebSocket.

ping_host(host)
    Convenience wrapper that sends a "ping" host_command.

send_pty_input(thread, text, *, approved)
    Phase 4: send a "pty.inject" command to the host daemon for the PTY
    session bound to *thread*.  Fail-closed: silently no-ops if the thread
    is not a driveable PTY (no host, no tmux_session_name).
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

log = logging.getLogger(__name__)


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


async def async_send_pty_input(thread, text: str, *, approved: bool = True) -> None:
    """Async variant of send_pty_input for use inside async contexts (e.g. Channels consumers).

    Awaits the channel layer group_send directly instead of wrapping it in
    async_to_sync, so it can be called from an already-running event loop.

    Parameters mirror send_pty_input — see that function's docstring.
    """
    from apps.threads.models import Thread

    if thread.runtime_mode != Thread.RuntimeModeChoices.PTY:
        log.warning("async_send_pty_input: thread %s is not PTY mode — no-op", thread.id)
        return

    host = thread.host
    if host is None:
        log.warning("async_send_pty_input: thread %s has no host — no-op", thread.id)
        return

    session_name = (thread.metadata or {}).get("tmux_session_name")
    if not session_name:
        log.warning("async_send_pty_input: thread %s has no tmux_session_name — no-op", thread.id)
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    group_name = f"host_{host.id}"
    event = {
        "type": "host_command",
        "command": "pty.inject",
        "session_name": session_name,
        "text": text,
        "approved": approved,
    }
    await channel_layer.group_send(group_name, event)


def send_pty_input(thread, text: str, *, approved: bool = True) -> None:
    """Phase 4: dispatch a pty.inject command to the host running *thread*.

    Fail-closed: silently no-ops when the thread is not a driveable PTY
    (no host linked, no tmux_session_name in metadata).  These guards are a
    second defensive layer — the approval gate in telegram/service.py already
    prevents reaching here for observed sessions, but we never trust the caller
    to have checked.

    The payload sent to the host contains ONLY session_name, text, and
    approved — no raw Telegram message data.  The text here is the exact string
    that was stored and approved in the Prompt, not re-read from any Telegram
    message.

    Parameters
    ----------
    thread:
        A ``Thread`` model instance for the PTY session.
    text:
        The exact text to inject (stored in the approved Prompt).
    approved:
        Must be True; enforced again inside PtySession.send_keys on the host.
    """
    from apps.threads.models import Thread

    if thread.runtime_mode != Thread.RuntimeModeChoices.PTY:
        log.warning("send_pty_input: thread %s is not PTY mode — no-op", thread.id)
        return

    host = thread.host
    if host is None:
        log.warning("send_pty_input: thread %s has no host — no-op", thread.id)
        return

    session_name = (thread.metadata or {}).get("tmux_session_name")
    if not session_name:
        log.warning("send_pty_input: thread %s has no tmux_session_name — no-op", thread.id)
        return

    send_host_command(
        host,
        "pty.inject",
        session_name=session_name,
        text=text,
        approved=approved,
    )
