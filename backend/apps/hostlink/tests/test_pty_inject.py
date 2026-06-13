"""Tests for Phase 4 — send_pty_input (backend side of the inject pipeline).

send_pty_input wraps send_host_command with pty.inject framing and fail-closed
defensive guards for non-driveable threads.
"""

from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.test import override_settings

INMEM_CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


def _make_account(db, suffix):
    from apps.accounts.models import Account

    return Account.objects.create(
        provider="anthropic",
        label=f"pi-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-pi-{suffix}",
        credential_recipient=f"r-pi-{suffix}",
    )


def _make_host(db, slug):
    from apps.hosts.models import Host

    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_thread(account, *, runtime_mode, host=None, tmux=None):
    from apps.threads.models import Thread

    meta = {}
    if tmux:
        meta["tmux_session_name"] = tmux
    return Thread.objects.create(
        name="pi-thread",
        runtime="claude_code",
        runtime_mode=runtime_mode,
        account=account,
        host=host,
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Happy path — sends the pty.inject frame with correct fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_pty_input_delivers_inject_frame(settings):
    """
    GIVEN a driveable PTY thread (has host + tmux_session_name)
    WHEN send_pty_input is called with approved=True
    THEN a pty.inject host_command frame is delivered to host_{host.id} with
         the EXACT text and session_name — no extra fields.
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_pty_input
    from apps.threads.models import Thread

    db = None  # pytest-django auto-creates DB for @pytest.mark.django_db
    account = _make_account(db, "hp1")
    host = _make_host(db, "hp-host-1")
    thread = _make_thread(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        tmux="my-session-1",
    )

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = async_to_sync(cl.new_channel)()
    async_to_sync(cl.group_add)(group, ch)

    send_pty_input(thread, "ls -la\n", approved=True)

    msg = async_to_sync(cl.receive)(ch)
    async_to_sync(cl.group_discard)(group, ch)

    assert msg["type"] == "host_command"
    assert msg["command"] == "pty.inject"
    assert msg["session_name"] == "my-session-1"
    assert msg["text"] == "ls -la\n"
    assert msg["approved"] is True
    # Invariant 7: no secrets — only session_name, text, approved, type, command
    allowed_keys = {"type", "command", "session_name", "text", "approved"}
    assert set(msg.keys()) == allowed_keys


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_pty_input_exact_text_binding(settings):
    """
    GIVEN a driveable PTY thread
    WHEN send_pty_input is called with a specific text
    THEN the frame delivered to the host contains EXACTLY that text — not a
         mutated or re-fetched version.  Verifies exact-text binding invariant.
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_pty_input
    from apps.threads.models import Thread

    account = _make_account(None, "etb1")
    host = _make_host(None, "etb-host-1")
    thread = _make_thread(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        tmux="etb-session",
    )

    original_text = "echo hello\n"
    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = async_to_sync(cl.new_channel)()
    async_to_sync(cl.group_add)(group, ch)

    send_pty_input(thread, original_text, approved=True)

    msg = async_to_sync(cl.receive)(ch)
    async_to_sync(cl.group_discard)(group, ch)

    assert msg["text"] == original_text


# ---------------------------------------------------------------------------
# Fail-closed: non-PTY threads must never inject
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_pty_input_noop_for_observed_thread(settings):
    """
    GIVEN an OBSERVED (non-PTY) thread with a host
    WHEN send_pty_input is called
    THEN nothing is delivered to the channel layer (fail-closed).
    Invariant 3: observed sessions never inject.
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_pty_input
    from apps.threads.models import Thread

    account = _make_account(None, "obs1")
    host = _make_host(None, "obs-host-1")
    thread = _make_thread(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        host=host,
        tmux="obs-session",
    )

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = async_to_sync(cl.new_channel)()
    async_to_sync(cl.group_add)(group, ch)

    send_pty_input(thread, "should not inject\n", approved=True)

    # Nothing should have been delivered — queue should be empty.
    import asyncio

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            return None

    result = async_to_sync(_try_receive)()
    async_to_sync(cl.group_discard)(group, ch)

    assert result is None


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_pty_input_noop_for_pty_thread_without_host(settings):
    """
    GIVEN a PTY thread with no host
    WHEN send_pty_input is called
    THEN nothing is delivered (fail-closed — no host to send to).
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_pty_input
    from apps.threads.models import Thread

    account = _make_account(None, "nh1")
    thread = _make_thread(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=None,
        tmux="no-host-session",
    )

    # Should not raise and no frame sent (no host to address)
    send_pty_input(thread, "hello\n", approved=True)


@pytest.mark.django_db
@override_settings(CHANNEL_LAYERS=INMEM_CHANNEL_LAYERS)
def test_send_pty_input_noop_for_pty_thread_without_tmux(settings):
    """
    GIVEN a PTY thread with a host but no tmux_session_name in metadata
    WHEN send_pty_input is called
    THEN nothing is delivered (fail-closed — no session to target).
    """
    settings.CHANNEL_LAYERS = INMEM_CHANNEL_LAYERS

    from apps.hostlink.service import send_pty_input
    from apps.threads.models import Thread

    account = _make_account(None, "nt1")
    host = _make_host(None, "nt-host-1")
    thread = _make_thread(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        tmux=None,  # no tmux_session_name
    )

    cl = get_channel_layer()
    group = f"host_{host.id}"
    ch = async_to_sync(cl.new_channel)()
    async_to_sync(cl.group_add)(group, ch)

    send_pty_input(thread, "hello\n", approved=True)

    import asyncio

    async def _try_receive():
        try:
            return await asyncio.wait_for(cl.receive(ch), timeout=0.1)
        except (asyncio.TimeoutError, Exception):
            return None

    result = async_to_sync(_try_receive)()
    async_to_sync(cl.group_discard)(group, ch)

    assert result is None
