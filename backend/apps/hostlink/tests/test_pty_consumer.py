"""Tests for PTY frame handling in HostDaemonConsumer."""

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
    return c


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

    async def test_pty_output_creates_message_and_delivers(self, monkeypatch):
        """
        GIVEN a PTY thread registered via pty_start
        WHEN a session.pty_output frame arrives
        THEN a Message is persisted and deliver_turn is called.
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

        # Message persisted
        from apps.threads.models import Message
        thread_id = consumer._pty_threads.get("orc-out-001")
        msgs = await sync_to_async(
            lambda: list(
                Message.objects.filter(thread_id=thread_id).values_list(
                    "redacted_content", flat=True
                )
            )
        )()
        assert "hello from PTY" in msgs

        # Delivery called
        assert len(delivery_calls) == 1
        assert delivery_calls[0][0]["text"] == "hello from PTY"
        assert delivery_calls[0][1] == -100888

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
        # The phase-4 placeholder should appear (not the read-only message)
        assert any(
            "phase 4" in m.lower() or "not wired" in m.lower()
            for m in sent_messages
        ), f"Expected phase-4 placeholder. Got: {sent_messages}"
