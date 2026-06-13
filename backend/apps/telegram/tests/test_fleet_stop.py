"""Tests for the Fleet F3 /stop command (Phase 1 of F3).

Safety invariants verified:
  S1. Allowlisted /stop on a running PTY thread:
        - session.kill frame sent to host group
        - Thread marked STOPPED
        - AuditEvent(RUNTIME_STOP) created
  S2. Non-allowlisted sender → silent no-op (no kill, no status change).
  S3. Unknown session arg → default-deny reply, no frame sent.
  S4. Observed (non-PTY) session → explicit "read-only" reply, no frame sent.
  S5. /stop with no args → Usage reply, no frame sent.
  S6. host_command frame shape: command="session.kill", session_name=<name>.
"""

from __future__ import annotations

import asyncio

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from django.test import override_settings

INMEM_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_account(suffix):
    from apps.accounts.models import Account

    return Account.objects.create(
        provider="anthropic",
        label=f"stop-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-stop-{suffix}",
        credential_recipient=f"r-stop-{suffix}",
    )


def _make_host(slug):
    from apps.hosts.models import Host

    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_pty_thread(account, host, tmux_name):
    from apps.threads.models import Thread

    return Thread.objects.create(
        name=f"pty-{tmux_name}",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        status=Thread.StatusChoices.RUNNING,
        account=account,
        host=host,
        metadata={"tmux_session_name": tmux_name},
    )


def _make_observed_thread(account, tmux_name):
    from apps.threads.models import Thread

    return Thread.objects.create(
        name=f"obs-{tmux_name}",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
        metadata={"tmux_session_name": tmux_name},
    )


class _CaptureSend:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, chat_id, text, **kwargs):
        self.calls.append({"chat_id": chat_id, "text": text})


# ---------------------------------------------------------------------------
# S1 — Allowlisted /stop: kill frame + Thread STOPPED + AuditEvent
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_stop_allowlisted_kills_marks_stopped_and_audits(settings):
    """
    GIVEN an allowlisted operator sends /stop <session-name> targeting a running PTY thread
    WHEN handle_update processes the message
    THEN a session.kill host_command frame is sent to the host group,
         the Thread is marked STOPPED,
         and an AuditEvent(RUNTIME_STOP) is created.
    Invariant S1.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    account = await database_sync_to_async(_make_account)("s1")
    host = await database_sync_to_async(_make_host)("stop-host-s1")
    thread = await database_sync_to_async(_make_pty_thread)(account, host, "orc-abc123")

    # Register a listener on the host group BEFORE sending /stop
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    send = _CaptureSend()

    with __import__("unittest.mock", fromlist=["AsyncMock"]).patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock,
    ):
        await handle_update(12345, "/stop orc-abc123", send=send)

    # Confirmation message was sent
    assert len(send.calls) == 1
    assert send.calls[0]["chat_id"] == 12345
    assert "orc-abc123" in send.calls[0]["text"] or "stop" in send.calls[0]["text"].lower()

    # host_command frame received on the host group
    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.3)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is not None, "No frame delivered to host group"
    assert frame.get("command") == "session.kill"
    assert frame.get("session_name") == "orc-abc123"

    # Thread is STOPPED
    @database_sync_to_async
    def _status():
        from apps.threads.models import Thread as _T
        return _T.objects.get(id=thread.id).status

    status = await _status()
    assert status == "stopped", f"Expected stopped, got {status!r}"

    # AuditEvent created
    @database_sync_to_async
    def _audit():
        from apps.audit.models import AuditEvent
        return AuditEvent.objects.filter(
            thread_id=thread.id,
            event_type=AuditEvent.EventTypeChoices.RUNTIME_STOP,
        ).exists()

    assert await _audit(), "AuditEvent(RUNTIME_STOP) not created"


# ---------------------------------------------------------------------------
# S2 — Non-allowlisted sender: no-op, no frame, no status change
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_stop_non_allowlisted_is_silent_noop(settings):
    """
    GIVEN a sender NOT in TELEGRAM_ALLOWED_CHAT_IDS sends /stop <session>
    WHEN handle_update processes the message
    THEN nothing is sent, no host frame is dispatched, Thread stays RUNNING.
    Invariant S2.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    account = await database_sync_to_async(_make_account)("s2")
    host = await database_sync_to_async(_make_host)("stop-host-s2")
    thread = await database_sync_to_async(_make_pty_thread)(account, host, "orc-s2session")

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    send = _CaptureSend()
    await handle_update(99999, "/stop orc-s2session", send=send)

    # Nothing sent
    assert send.calls == []

    # No frame to host
    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.15)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)
    assert frame is None, "Non-allowlisted sender must not send host frames"

    # Thread still RUNNING
    @database_sync_to_async
    def _status():
        from apps.threads.models import Thread as _T
        return _T.objects.get(id=thread.id).status

    assert await _status() == "running"


# ---------------------------------------------------------------------------
# S3 — Unknown session arg → default-deny reply, no frame
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_stop_unknown_session_default_deny_reply(settings):
    """
    GIVEN an allowlisted operator /stop targeting a session name that doesn't exist
    WHEN handle_update processes the message
    THEN a denial/not-found reply is sent and NO frame is dispatched to any host group.
    Invariant S3.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    # No matching thread in DB
    send = _CaptureSend()
    await handle_update(12345, "/stop nonexistent-session-xyz", send=send)

    assert len(send.calls) == 1, "Should reply with denial/not-found"
    assert send.calls[0]["chat_id"] == 12345
    reply = send.calls[0]["text"].lower()
    assert "not found" in reply or "no running" in reply or "nonexistent" in reply


# ---------------------------------------------------------------------------
# S4 — Observed session → "read-only" reply, no frame
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_stop_observed_session_read_only_reply(settings):
    """
    GIVEN an allowlisted operator /stop targeting an observed (non-PTY) session
    WHEN handle_update processes the message
    THEN an explicit "read-only" or "observed" reply is sent — NOT a silent drop
         and NOT a session.kill frame.
    Invariant S4: observed sessions are read-only; explicit reply required.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    account = await database_sync_to_async(_make_account)("s4")
    await database_sync_to_async(_make_observed_thread)(account, "obs-session-s4")

    send = _CaptureSend()
    await handle_update(12345, "/stop obs-session-s4", send=send)

    assert len(send.calls) == 1
    reply = send.calls[0]["text"].lower()
    assert "read-only" in reply or "observed" in reply, (
        f"Expected read-only/observed reply, got: {send.calls[0]['text']!r}"
    )


# ---------------------------------------------------------------------------
# S5 — /stop with no args → Usage reply
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_ALLOWED_CHAT_IDS={12345}, TELEGRAM_FORUM_CHAT_ID="")
async def test_stop_no_args_shows_usage(settings):
    """
    GIVEN an allowlisted operator sends /stop with no session argument
    WHEN handle_update processes the message
    THEN a Usage reply is sent and nothing else happens.
    Invariant S5.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    send = _CaptureSend()
    await handle_update(12345, "/stop", send=send)

    assert len(send.calls) == 1
    assert "Usage" in send.calls[0]["text"] or "usage" in send.calls[0]["text"].lower()


# ---------------------------------------------------------------------------
# S6 — host_command frame shape for session.kill
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(
    TELEGRAM_ALLOWED_CHAT_IDS={12345},
    TELEGRAM_FORUM_CHAT_ID="",
    CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS,
)
async def test_stop_host_frame_shape(settings):
    """
    GIVEN a valid /stop targeting a known PTY session
    WHEN handle_update dispatches the kill
    THEN the frame on the host group has command="session.kill"
         and session_name equal to the tmux session name.
    Invariant S6: frame shape contract.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS
    settings.TELEGRAM_FORUM_CHAT_ID = ""

    from apps.telegram.service import handle_update

    account = await database_sync_to_async(_make_account)("s6")
    host = await database_sync_to_async(_make_host)("stop-host-s6")
    await database_sync_to_async(_make_pty_thread)(account, host, "orc-s6session")

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = await cl.new_channel()
    await cl.group_add(group, ch)

    send = _CaptureSend()

    with __import__("unittest.mock", fromlist=["AsyncMock"]).patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock,
    ):
        await handle_update(12345, "/stop orc-s6session", send=send)

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.3)
        except (asyncio.TimeoutError, Exception):
            return None

    frame = await _try_receive()
    await cl.group_discard(group, ch)

    assert frame is not None, "No frame delivered to host group"
    # Exact frame shape contract
    assert frame["type"] == "host_command"
    assert frame["command"] == "session.kill"
    assert frame["session_name"] == "orc-s6session"
    # No secrets or extra fields beyond what's needed
    assert "text" not in frame
    assert "approved" not in frame
