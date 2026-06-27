"""Tests for headless Claude relay — consumer-side frame handlers.

Covers session.headless_start and session.headless_reply dispatch.
"""

from __future__ import annotations

import uuid

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host
from apps.threads.models import Thread


def _make_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    c._pty_threads = {}
    return c


# ---------------------------------------------------------------------------
# session.headless_start
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100777")
async def test_headless_start_creates_thread_with_correct_metadata(monkeypatch):
    """
    GIVEN a session.headless_start frame with session_name, claude_session_id, cwd
    WHEN the consumer handles it
    THEN a Thread exists with runtime_mode=PTY, metadata.headless=True, and the
         claude_session_id stored, and _deliver_to_telegram is called once.
    """
    delivered = []

    async def fake_deliver(thread, parsed):
        delivered.append((thread, parsed))

    host = await sync_to_async(Host.objects.create)(slug="hl-s1", name="hl-s1", os="linux")
    consumer = _make_consumer(host)
    monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

    session_name = "headless-abc"
    claude_session_id = str(uuid.uuid4())

    await consumer._handle_headless_start(
        {
            "session_name": session_name,
            "claude_session_id": claude_session_id,
            "cwd": "/home/user/project",
        }
    )

    thread = await sync_to_async(Thread.objects.filter(external_session_ref=session_name).get)()
    assert thread.runtime_mode == Thread.RuntimeModeChoices.PTY
    assert thread.status == Thread.StatusChoices.RUNNING
    assert thread.metadata["headless"] is True
    assert thread.metadata["claude_session_id"] == claude_session_id
    assert thread.metadata["cwd"] == "/home/user/project"
    assert thread.metadata["tmux_session_name"] is None
    assert thread.host_id == host.id

    # delivery was attempted with the intro message
    assert len(delivered) == 1
    _t, parsed = delivered[0]
    assert parsed["role"] == "assistant"
    assert "headless" in parsed["text"].lower() or "ready" in parsed["text"].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_start_missing_fields_is_noop(monkeypatch):
    """
    GIVEN a session.headless_start frame missing claude_session_id
    WHEN the consumer handles it
    THEN no Thread is created and no delivery is attempted.
    """
    delivered = []

    async def fake_deliver(thread, parsed):
        delivered.append(1)

    host = await sync_to_async(Host.objects.create)(slug="hl-s2", name="hl-s2", os="linux")
    consumer = _make_consumer(host)
    monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

    await consumer._handle_headless_start({"session_name": "only-name"})

    count = await sync_to_async(Thread.objects.filter(external_session_ref="only-name").count)()
    assert count == 0
    assert delivered == []


# ---------------------------------------------------------------------------
# session.headless_reply
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100777")
async def test_headless_reply_records_turn_and_marks_started(monkeypatch):
    """
    GIVEN a headless Thread and a session.headless_reply frame for its id
    WHEN the consumer handles it
    THEN an assistant Message is recorded and metadata.claude_session_started=True.
    """
    from apps.accounts.models import Account
    from apps.threads.models import Message

    delivered = []

    async def fake_deliver(thread, parsed):
        delivered.append((thread, parsed))

    host = await sync_to_async(Host.objects.create)(slug="hl-r1", name="hl-r1", os="linux")
    account = await sync_to_async(Account.objects.create)(
        provider="pty",
        label="orc-run-hl",
        auth_type="none",
        credential_type="none",
    )
    thread = await sync_to_async(Thread.objects.create)(
        name="headless-reply-test",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        account=account,
        status=Thread.StatusChoices.RUNNING,
        metadata={
            "headless": True,
            "claude_session_id": str(uuid.uuid4()),
            "cwd": "/tmp",
            "tmux_session_name": None,
        },
    )

    consumer = _make_consumer(host)
    monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

    await consumer._handle_headless_reply(
        {"thread_id": str(thread.id), "text": "Here is your answer.", "is_error": False}
    )

    # Turn recorded
    msg_count = await sync_to_async(
        Message.objects.filter(thread=thread, role="assistant").count
    )()
    assert msg_count == 1

    # Delivery attempted
    assert len(delivered) == 1
    _t, parsed = delivered[0]
    assert parsed["text"] == "Here is your answer."
    assert parsed["role"] == "assistant"

    # claude_session_started flag set
    refreshed = await sync_to_async(Thread.objects.get)(id=thread.id)
    assert refreshed.metadata.get("claude_session_started") is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_reply_missing_thread_id_is_noop(monkeypatch):
    """
    GIVEN a session.headless_reply frame with no thread_id
    WHEN the consumer handles it
    THEN no DB write and no delivery.
    """
    delivered = []

    async def fake_deliver(thread, parsed):
        delivered.append(1)

    host = await sync_to_async(Host.objects.create)(slug="hl-r2", name="hl-r2", os="linux")
    consumer = _make_consumer(host)
    monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

    await consumer._handle_headless_reply({"text": "some text"})

    assert delivered == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_reply_nonexistent_thread_is_noop(monkeypatch):
    """
    GIVEN a session.headless_reply frame with a thread_id that does not exist
    WHEN the consumer handles it
    THEN no error is raised and no delivery is attempted.
    """
    delivered = []

    async def fake_deliver(thread, parsed):
        delivered.append(1)

    host = await sync_to_async(Host.objects.create)(slug="hl-r3", name="hl-r3", os="linux")
    consumer = _make_consumer(host)
    monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

    await consumer._handle_headless_reply(
        {"thread_id": str(uuid.uuid4()), "text": "answer", "is_error": False}
    )

    assert delivered == []
