"""Tests for the session.turn frame handler and the tail.start reconnect resync.

session.turn is forwarded by the host daemon when it tails a bound Claude
session's JSONL transcript — one frame per editor-typed turn. Persistence
must be idempotent at the DB layer (record_turn's source_event_key
constraint), since the daemon can re-send the same transcript event after a
restart or ws reconnect.
"""

from __future__ import annotations

import uuid

import pytest
from asgiref.sync import sync_to_async

from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host
from apps.threads.models import Message, Thread


def _make_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    c._pty_threads = {}
    return c


def _make_host(slug):
    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_headless_thread(host, *, claude_session_id="sid-1", status=Thread.StatusChoices.RUNNING):
    from apps.accounts.models import Account

    account, _ = Account.objects.get_or_create(
        provider="pty",
        label="orc-run",
        defaults={"auth_type": "none", "credential_type": "none"},
    )
    return Thread.objects.create(
        name="headless-turn-test",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        account=account,
        status=status,
        metadata={"headless": True, "claude_session_id": claude_session_id, "cwd": "/tmp/proj"},
    )


# ---------------------------------------------------------------------------
# session.turn handler
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestSessionTurnHandler:

    async def test_valid_frame_persists_and_queues_delivery(self, monkeypatch):
        """
        GIVEN a valid session.turn frame for a thread owned by this host
        WHEN the consumer handles it
        THEN a Message is recorded and delivery is queued.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append((thread, parsed))

        host = await sync_to_async(_make_host)("turn-host-1")
        thread = await sync_to_async(_make_headless_thread)(host)
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": str(thread.id),
                "claude_session_id": "sid-1",
                "role": "assistant",
                "text": "the answer",
                "source_event_key": "evt-1",
            }
        )

        count = await sync_to_async(Message.objects.filter(thread=thread).count)()
        assert count == 1
        assert len(delivered) == 1
        _t, parsed = delivered[0]
        assert parsed["text"] == "the answer"
        assert parsed["role"] == "assistant"

    async def test_duplicate_frame_does_not_deliver_twice(self, monkeypatch):
        """
        GIVEN a session.turn frame already persisted with a given source_event_key
        WHEN the SAME frame is handled again
        THEN no second Message row is created and delivery is not queued again.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append((thread, parsed))

        host = await sync_to_async(_make_host)("turn-host-2")
        thread = await sync_to_async(_make_headless_thread)(host)
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        frame = {
            "thread_id": str(thread.id),
            "claude_session_id": "sid-1",
            "role": "assistant",
            "text": "the answer",
            "source_event_key": "evt-dup",
        }
        await consumer._handle_session_turn(frame)
        await consumer._handle_session_turn(frame)

        count = await sync_to_async(Message.objects.filter(thread=thread).count)()
        assert count == 1
        assert len(delivered) == 1

    async def test_user_role_gets_operator_prefix(self, monkeypatch):
        """
        GIVEN a session.turn frame with role=user
        WHEN the consumer handles it
        THEN the delivered text is prefixed with "🧑 " so it's visually distinct.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append((thread, parsed))

        host = await sync_to_async(_make_host)("turn-host-3")
        thread = await sync_to_async(_make_headless_thread)(host)
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": str(thread.id),
                "claude_session_id": "sid-1",
                "role": "user",
                "text": "do the thing",
                "source_event_key": "evt-user-1",
            }
        )

        assert len(delivered) == 1
        _t, parsed = delivered[0]
        assert parsed["text"] == "🧑 do the thing"
        assert parsed["role"] == "user"

    async def test_foreign_host_thread_is_dropped(self, monkeypatch):
        """
        GIVEN a thread owned by a DIFFERENT host
        WHEN a session.turn frame for that thread arrives on this host's connection
        THEN it is dropped — no Message row, no delivery.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append(1)

        host_a = await sync_to_async(_make_host)("turn-host-a")
        host_b = await sync_to_async(_make_host)("turn-host-b")
        other_thread = await sync_to_async(_make_headless_thread)(host_b)
        consumer = _make_consumer(host_a)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": str(other_thread.id),
                "claude_session_id": "sid-1",
                "role": "assistant",
                "text": "text",
                "source_event_key": "evt-foreign",
            }
        )

        count = await sync_to_async(Message.objects.filter(thread=other_thread).count)()
        assert count == 0
        assert delivered == []

    async def test_bad_role_is_dropped(self, monkeypatch):
        """
        GIVEN a session.turn frame with an invalid role
        WHEN the consumer handles it
        THEN it is dropped — no Message row, no delivery.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append(1)

        host = await sync_to_async(_make_host)("turn-host-4")
        thread = await sync_to_async(_make_headless_thread)(host)
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": str(thread.id),
                "claude_session_id": "sid-1",
                "role": "system",
                "text": "text",
                "source_event_key": "evt-badrole",
            }
        )

        count = await sync_to_async(Message.objects.filter(thread=thread).count)()
        assert count == 0
        assert delivered == []

    async def test_invalid_thread_id_is_dropped(self, monkeypatch):
        """
        GIVEN a session.turn frame with a thread_id that is not a valid UUID
        WHEN the consumer handles it
        THEN it is dropped without raising.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append(1)

        host = await sync_to_async(_make_host)("turn-host-5")
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": "not-a-uuid",
                "claude_session_id": "sid-1",
                "role": "assistant",
                "text": "text",
                "source_event_key": "evt-badid",
            }
        )

        assert delivered == []

    async def test_missing_source_event_key_is_dropped(self, monkeypatch):
        """
        GIVEN a session.turn frame with an empty source_event_key
        WHEN the consumer handles it
        THEN it is dropped — no Message row, no delivery.
        """
        delivered = []

        async def fake_deliver(thread, parsed):
            delivered.append(1)

        host = await sync_to_async(_make_host)("turn-host-6")
        thread = await sync_to_async(_make_headless_thread)(host)
        consumer = _make_consumer(host)
        monkeypatch.setattr(consumer, "_deliver_to_telegram", fake_deliver)

        await consumer._handle_session_turn(
            {
                "thread_id": str(thread.id),
                "claude_session_id": "sid-1",
                "role": "assistant",
                "text": "text",
                "source_event_key": "",
            }
        )

        count = await sync_to_async(Message.objects.filter(thread=thread).count)()
        assert count == 0
        assert delivered == []


# ---------------------------------------------------------------------------
# reconnect resync — tail.start sent for this host's running headless threads
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestResyncTailSessions:

    async def test_two_running_headless_threads_send_two_tail_start_frames(self):
        """
        GIVEN a host with 2 RUNNING headless threads (metadata has claude_session_id)
        WHEN the consumer's reconnect resync runs
        THEN 2 tail.start frames are sent directly on this connection.
        """
        sent = []

        host = await sync_to_async(_make_host)("resync-host-1")
        thread_a = await sync_to_async(_make_headless_thread)(host, claude_session_id="sid-a")
        thread_b = await sync_to_async(_make_headless_thread)(host, claude_session_id="sid-b")
        consumer = _make_consumer(host)

        async def fake_send_json(payload):
            sent.append(payload)

        consumer.send_json = fake_send_json

        await consumer._resync_tail_sessions()

        assert len(sent) == 2
        thread_ids = {f["thread_id"] for f in sent}
        assert thread_ids == {str(thread_a.id), str(thread_b.id)}
        for frame in sent:
            assert frame["type"] == "host_command"
            assert frame["command"] == "tail.start"
            assert frame["provider"] == "claude"

    async def test_non_headless_thread_not_resent(self):
        """
        GIVEN a RUNNING thread with no claude_session_id in metadata (e.g. a tmux PTY session)
        WHEN the reconnect resync runs
        THEN no tail.start frame is sent for it.
        """
        from apps.accounts.models import Account

        sent = []

        host = await sync_to_async(_make_host)("resync-host-2")

        def _make_tmux_thread():
            account, _ = Account.objects.get_or_create(
                provider="pty", label="orc-run",
                defaults={"auth_type": "none", "credential_type": "none"},
            )
            return Thread.objects.create(
                name="tmux-thread",
                runtime="pty",
                runtime_mode=Thread.RuntimeModeChoices.PTY,
                host=host,
                account=account,
                status=Thread.StatusChoices.RUNNING,
                metadata={"tmux_session_name": "sess-1"},
            )

        await sync_to_async(_make_tmux_thread)()
        consumer = _make_consumer(host)

        async def fake_send_json(payload):
            sent.append(payload)

        consumer.send_json = fake_send_json

        await consumer._resync_tail_sessions()

        assert sent == []
