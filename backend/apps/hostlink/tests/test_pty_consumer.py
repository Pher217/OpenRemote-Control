"""Tests for PTY frame handling in HostDaemonConsumer."""

import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from apps.hostlink import consumers
from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host
from apps.threads.models import Thread


def _make_pty_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    c._file_sessions = {}
    c._pty_threads = {}
    # Delivery is offloaded to a background drainer (started in connect()); tests
    # set the queue directly and drain it explicitly.
    c._delivery_queue = asyncio.Queue(maxsize=2000)
    return c


async def _drain(consumer, forum_chat_id):
    """Process the background delivery queue synchronously (drainer is off-loop in prod)."""
    while not consumer._delivery_queue.empty():
        thread, parsed = consumer._delivery_queue.get_nowait()
        await consumers.deliver_turn(thread, parsed, None, forum_chat_id=forum_chat_id)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
class TestPtyFrameHandling:

    async def test_pty_start_creates_pty_thread(self):
        """
        GIVEN a connected daemon consumer
        WHEN a session.pty_start frame is received
        THEN a Thread is created with runtime_mode=PTY, tmux_session_name in metadata, and host stamped.
        """
        host = await sync_to_async(Host.objects.create)(slug="pty-host-1", os="linux")
        consumer = _make_pty_consumer(host)

        await consumer._handle_pty_start({
            "session_name": "orc-test-abc",
            "command": "claude",
            "cwd": "/tmp",
        })

        thread = await sync_to_async(
            lambda: Thread.objects.filter(external_session_ref="orc-test-abc").first()
        )()
        assert thread is not None
        assert thread.runtime_mode == Thread.RuntimeModeChoices.PTY
        assert thread.metadata["tmux_session_name"] == "orc-test-abc"
        assert thread.host_id == host.id
        assert thread.status == Thread.StatusChoices.RUNNING

    async def test_pty_start_registers_thread_in_consumer(self):
        """
        GIVEN a pty_start frame
        WHEN handled
        THEN the consumer's _pty_threads dict maps session_name -> thread.id (str).
        """
        host = await sync_to_async(Host.objects.create)(slug="pty-host-2", os="linux")
        consumer = _make_pty_consumer(host)

        await consumer._handle_pty_start({
            "session_name": "orc-reg-xyz",
            "command": "codex",
            "cwd": "",
        })

        assert "orc-reg-xyz" in consumer._pty_threads
        thread_id = consumer._pty_threads["orc-reg-xyz"]
        thread = await sync_to_async(Thread.objects.get)(id=thread_id)
        assert thread.external_session_ref == "orc-reg-xyz"

    async def test_pty_start_keys_thread_by_claude_session_id(self):
        """
        GIVEN a pty_start frame carrying a claude_session_id (the --session-id UUID)
        WHEN handled
        THEN the Thread is keyed by that UUID (not the tmux name) and the tmux
             session name is stored in metadata for input.
        """
        host = await sync_to_async(Host.objects.create)(slug="pty-host-sid", os="linux")
        consumer = _make_pty_consumer(host)

        await consumer._handle_pty_start({
            "session_name": "remote",
            "command": "claude --session-id 11111111-1111-1111-1111-111111111111",
            "cwd": "/tmp",
            "claude_session_id": "11111111-1111-1111-1111-111111111111",
        })

        thread = await sync_to_async(
            lambda: Thread.objects.filter(
                external_session_ref="11111111-1111-1111-1111-111111111111"
            ).first()
        )()
        assert thread is not None
        assert thread.metadata["tmux_session_name"] == "remote"
        assert thread.metadata["claude_session_id"] == "11111111-1111-1111-1111-111111111111"
        assert thread.runtime_mode == Thread.RuntimeModeChoices.PTY

    async def test_pty_start_upgrades_existing_observed_thread(self):
        """
        GIVEN a thread already created by transcript observation, keyed by the
              claude session UUID, with no tmux session name (read-only observed)
        WHEN a pty_start frame arrives for the same UUID
        THEN the existing thread is upgraded to driveable PTY with the tmux name
             attached — one canonical thread, not a duplicate.
        """
        from apps.accounts.models import Account

        host = await sync_to_async(Host.objects.create)(slug="pty-host-up", os="linux")
        consumer = _make_pty_consumer(host)
        account = await sync_to_async(Account.objects.create)(
            provider="claude_code", label="observer",
            auth_type="none", credential_type="none",
        )
        sid = "22222222-2222-2222-2222-222222222222"
        observed = await sync_to_async(Thread.objects.create)(
            external_session_ref=sid,
            name="observed claude",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
            account=account,
            status=Thread.StatusChoices.RUNNING,
            metadata={},
        )

        await consumer._handle_pty_start({
            "session_name": "remote2",
            "command": f"claude --session-id {sid}",
            "cwd": "/tmp",
            "claude_session_id": sid,
        })

        count = await sync_to_async(
            lambda: Thread.objects.filter(external_session_ref=sid).count()
        )()
        assert count == 1
        await sync_to_async(observed.refresh_from_db)()
        assert observed.runtime_mode == Thread.RuntimeModeChoices.PTY
        assert observed.metadata["tmux_session_name"] == "remote2"
        assert observed.host_id == host.id

    async def test_pty_output_records_debug_and_does_not_deliver(self, monkeypatch):
        """
        GIVEN a PTY thread registered via pty_start
        WHEN a session.pty_output frame arrives (raw TUI screen frame)
        THEN it is persisted as debug telemetry (metadata.source="pty_screen") and
             is NOT delivered to Telegram — clean output comes only from the JSONL
             transcript path. (drive-unify PR 2)
        """
        delivery_calls = []

        async def fake_deliver(thread, parsed, msg, *, forum_chat_id):
            delivery_calls.append((parsed, forum_chat_id))

        monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)

        host = await sync_to_async(Host.objects.create)(slug="pty-host-3", os="linux")
        consumer = _make_pty_consumer(host)

        # First, create the thread via pty_start
        await consumer._handle_pty_start({
            "session_name": "orc-out-001",
            "command": "echo hi",
            "cwd": "",
        })

        # Then send output
        with override_settings(TELEGRAM_FORUM_CHAT_ID="-100888"):
            await consumer._handle_pty_output({
                "session_name": "orc-out-001",
                "text": "hello from PTY",
            })

        # Message persisted, tagged as pty_screen debug telemetry
        from apps.threads.models import Message
        thread_id = consumer._pty_threads.get("orc-out-001")
        msgs = await sync_to_async(
            lambda: list(
                Message.objects.filter(thread_id=thread_id).values_list(
                    "redacted_content", "metadata"
                )
            )
        )()
        assert ("hello from PTY", {"source": "pty_screen"}) in msgs

        # Telegram delivery NOT called for raw PTY frames
        assert delivery_calls == []

    async def test_jsonl_turn_attaches_to_pty_thread_and_delivers(self, monkeypatch):
        """
        GIVEN a driveable PTY thread keyed by a claude session UUID
        WHEN a clean JSONL turn (session.event) arrives for that same UUID
        THEN it attaches to the SAME thread (no duplicate) and IS delivered to
             Telegram — clean output and input share one topic. (drive-unify PR 2)
        """
        delivery_calls = []

        async def fake_deliver(thread, parsed, msg, *, forum_chat_id):
            delivery_calls.append((str(thread.id), parsed["text"]))

        monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)

        host = await sync_to_async(Host.objects.create)(slug="pty-host-uni", os="linux")
        consumer = _make_pty_consumer(host)
        sid = "33333333-3333-3333-3333-333333333333"

        await consumer._handle_pty_start({
            "session_name": "remote-uni",
            "command": f"claude --session-id {sid}",
            "cwd": "/tmp",
            "claude_session_id": sid,
        })
        pty_thread_id = consumer._pty_threads["remote-uni"]

        with override_settings(TELEGRAM_FORUM_CHAT_ID="-100999"):
            await consumer._handle_session_event({
                "session_id": sid,
                "jsonl_path": f"/x/{sid}.jsonl",
                "provider": "claude_code",
                "role": "assistant",
                "text": "the real answer",
            })
            # Turn is enqueued; the drainer is what calls deliver_turn.
            await _drain(consumer, -100999)

        # Same thread, no duplicate
        total = await sync_to_async(
            lambda: Thread.objects.filter(external_session_ref=sid).count()
        )()
        assert total == 1
        # Clean turn delivered, attributed to the unified PTY thread
        assert delivery_calls == [(pty_thread_id, "the real answer")]

    async def test_pty_end_marks_thread_completed(self):
        """
        GIVEN a PTY thread registered via pty_start
        WHEN a session.pty_end frame arrives
        THEN the Thread status is COMPLETED.
        """
        host = await sync_to_async(Host.objects.create)(slug="pty-host-4", os="linux")
        consumer = _make_pty_consumer(host)

        await consumer._handle_pty_start({
            "session_name": "orc-end-001",
            "command": "bash",
            "cwd": "",
        })

        thread_id = consumer._pty_threads["orc-end-001"]

        await consumer._handle_pty_end({"session_name": "orc-end-001"})

        thread = await sync_to_async(Thread.objects.get)(id=thread_id)
        assert thread.status == Thread.StatusChoices.COMPLETED

    async def test_pty_thread_is_driveable(self, monkeypatch):
        """
        GIVEN a Thread created by session.pty_start (runtime_mode=PTY, host set, tmux_session_name in metadata)
        WHEN handle_forum_reply's read-only guard evaluates it
        THEN the guard does NOT send the read-only message — the thread IS driveable.

        This is the key test proving Phase 3 makes PTY threads injectable (Phase 4 will wire
        the actual injection).
        """
        from apps.telegram.service import handle_forum_reply

        host = await sync_to_async(Host.objects.create)(slug="pty-host-5", os="linux")
        consumer = _make_pty_consumer(host)

        await consumer._handle_pty_start({
            "session_name": "orc-drive-001",
            "command": "claude",
            "cwd": "",
        })

        thread_id = consumer._pty_threads["orc-drive-001"]

        # Add telegram topic metadata so _lookup_thread_for_topic finds it
        await sync_to_async(Thread.objects.filter(id=thread_id).update)(
            metadata={
                "tmux_session_name": "orc-drive-001",
                "command": "claude",
                "cwd": "",
                "telegram_topic_id": 42,
                "telegram_forum_chat_id": -100777,
            }
        )

        sent_messages = []

        async def fake_send(chat_id, text, **kwargs):
            sent_messages.append(text)

        with override_settings(
            TELEGRAM_FORUM_CHAT_ID="-100777",
            TELEGRAM_ALLOWED_CHAT_IDS={9999},
        ):
            await handle_forum_reply(
                forum_chat_id=-100777,
                message_thread_id=42,
                from_user_id=9999,
                text="do something",
                send=fake_send,
            )

        # The read-only guard message must NOT appear
        read_only_msg = "This session is read-only"
        for msg in sent_messages:
            assert read_only_msg not in msg, (
                f"Thread was incorrectly treated as read-only. Messages: {sent_messages}"
            )
        # Phase 5: an approval prompt message should be sent (not a phase-4 stub)
        assert any(
            "inject" in m.lower()
            for m in sent_messages
        ), f"Expected an approval prompt message for driveable session. Got: {sent_messages}"
