"""Tests for the wsclient "tail.start"/"tail.stop" host commands and their
interaction with headless.prompt (two-writer suppression).

Invariants verified:
  - "tail.start" creates a TranscriptTail in the registry; once it detects a
    turn, a session.turn frame is enqueued via the same outbound queue used
    by other reply-producing handlers.
  - "tail.start" called twice with the same session_id+cwd is idempotent.
  - "tail.stop" stops and removes the tail from the registry.
  - drive_started/drive_finished are invoked around a headless.prompt run.

claude_transcript_path is monkeypatched to a tmp_path fixture in every test —
these tests never touch the real ~/.claude directory.
"""

from __future__ import annotations

import asyncio
import json

import pytest

import agent_host.wsclient as wsclient_module
from agent_host.wsclient import handle_host_command


async def _new_tasks(loop, before: set) -> list[asyncio.Task]:
    """Return tasks created on *loop* since *before* was captured."""
    return [t for t in asyncio.all_tasks(loop) if t not in before]


@pytest.fixture(autouse=True)
def _clear_registries():
    """Ensure module-level registries don't leak state across tests."""
    wsclient_module._transcript_tails.clear()
    wsclient_module._headless_locks.clear()
    yield
    wsclient_module._transcript_tails.clear()
    wsclient_module._headless_locks.clear()


@pytest.fixture
def fake_transcript(monkeypatch, tmp_path):
    """Point claude_transcript_path at a controllable tmp file."""
    transcript_file = tmp_path / "transcript.jsonl"

    def fake_path(cwd, session_id):
        return str(transcript_file) if transcript_file.exists() else None

    monkeypatch.setattr(
        "agent_host.transcript_tail.claude_transcript_path", fake_path
    )
    monkeypatch.setattr("agent_host.transcript_tail.POLL_INTERVAL", 0.01)
    return transcript_file


def _assistant_line(uuid: str, text: str) -> bytes:
    ev = {
        "type": "assistant",
        "uuid": uuid,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    return (json.dumps(ev) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# tail.start creates a tail and forwards turns via the outbound queue
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_start_creates_tail_and_forwards_turn(fake_transcript):
    """
    GIVEN a tail.start command with a valid claude_session_id/cwd/thread_id
    WHEN handle_host_command is called from a running event loop
    THEN a TranscriptTail is registered, and once it detects a new turn a
         session.turn frame is enqueued via the outbound queue.
    """
    outbound: asyncio.Queue = asyncio.Queue()

    # File exists already (pre-tail-start content should NOT be replayed).
    fake_transcript.write_bytes(_assistant_line("pre-1", "old"))

    frame = {
        "type": "host_command",
        "command": "tail.start",
        "thread_id": "thread-1",
        "claude_session_id": "sess-tail-1",
        "cwd": "/tmp/proj",
        "provider": "claude",
    }
    handle_host_command(frame, incoming_queue=outbound)

    assert "sess-tail-1" in wsclient_module._transcript_tails

    with open(fake_transcript, "ab") as f:
        f.write(_assistant_line("new-1", "hello from editor"))

    # Give the poll loop a few cycles to pick it up. The window is generous
    # (2s worst-case) because the shared default executor can be saturated
    # under full-suite ordering; the loop breaks immediately on success.
    for _ in range(100):
        if not outbound.empty():
            break
        await asyncio.sleep(0.02)

    assert not outbound.empty(), "expected a session.turn frame to be enqueued"
    event = outbound.get_nowait()
    assert event["type"] == "session.turn"
    assert event["data"]["thread_id"] == "thread-1"
    assert event["data"]["claude_session_id"] == "sess-tail-1"
    assert event["data"]["role"] == "assistant"
    assert event["data"]["text"] == "hello from editor"
    assert event["data"]["source_event_key"] == "new-1"

    # Cleanup: stop the tail so its background task doesn't leak past the test.
    tail = wsclient_module._transcript_tails.pop("sess-tail-1")
    await tail.stop()


# ---------------------------------------------------------------------------
# tail.start idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_start_twice_same_session_and_cwd_is_idempotent(fake_transcript):
    """
    GIVEN tail.start called once for a session_id+cwd
    WHEN tail.start is called again with the identical session_id+cwd
    THEN no second TranscriptTail is created (registry entry is unchanged).
    """
    outbound: asyncio.Queue = asyncio.Queue()
    frame = {
        "type": "host_command",
        "command": "tail.start",
        "thread_id": "thread-1",
        "claude_session_id": "sess-tail-2",
        "cwd": "/tmp/proj",
        "provider": "claude",
    }
    handle_host_command(frame, incoming_queue=outbound)
    first_tail = wsclient_module._transcript_tails["sess-tail-2"]

    handle_host_command(frame, incoming_queue=outbound)
    second_tail = wsclient_module._transcript_tails["sess-tail-2"]

    assert first_tail is second_tail

    tail = wsclient_module._transcript_tails.pop("sess-tail-2")
    await tail.stop()


# ---------------------------------------------------------------------------
# tail.stop removes and stops the tail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tail_stop_removes_tail_from_registry(fake_transcript):
    """
    GIVEN a running tail for a claude_session_id
    WHEN tail.stop is called for that claude_session_id
    THEN the tail is removed from the registry.
    """
    outbound: asyncio.Queue = asyncio.Queue()
    start_frame = {
        "type": "host_command",
        "command": "tail.start",
        "thread_id": "thread-1",
        "claude_session_id": "sess-tail-3",
        "cwd": "/tmp/proj",
        "provider": "claude",
    }
    handle_host_command(start_frame, incoming_queue=outbound)
    assert "sess-tail-3" in wsclient_module._transcript_tails

    stop_frame = {
        "type": "host_command",
        "command": "tail.stop",
        "claude_session_id": "sess-tail-3",
    }
    handle_host_command(stop_frame, incoming_queue=outbound)

    assert "sess-tail-3" not in wsclient_module._transcript_tails
    # Let the scheduled stop() task run to completion so it doesn't leak.
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# drive_started/drive_finished invoked around a headless.prompt run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_headless_prompt_suppresses_and_resumes_tail(monkeypatch, fake_transcript):
    """
    GIVEN a registered tail for a claude_session_id
    WHEN a headless.prompt runs for that same claude_session_id
    THEN drive_started() suppresses the tail for the duration of the run and
         drive_finished(success=True) is invoked once the run completes
         successfully (verified via the tail's public suppression state).
    """
    from agent_host.transcript_tail import TranscriptTail

    calls: list[tuple[str, bool | None]] = []

    class SpyTail(TranscriptTail):
        def drive_started(self) -> None:
            calls.append(("started", None))
            super().drive_started()

        def drive_finished(self, success: bool) -> None:
            calls.append(("finished", success))
            super().drive_finished(success)

    loop = asyncio.get_running_loop()
    tail = SpyTail("sess-tail-4", "/tmp/proj", emit=lambda ev: None, loop=loop)
    tail.start()
    wsclient_module._transcript_tails["sess-tail-4"] = tail

    def fake_run_headless_streaming(prompt, claude_session_id, cwd, started, on_event):
        # Runs "on the thread" synchronously in this fake — asserts suppression
        # is already active by the time the headless run is executing.
        assert tail._suppress is True
        return {"text": "done", "is_error": False}

    monkeypatch.setattr(
        "agent_host.claude_headless.run_headless_streaming", fake_run_headless_streaming
    )

    outbound: asyncio.Queue = asyncio.Queue()
    frame = {
        "type": "host_command",
        "command": "headless.prompt",
        "claude_session_id": "sess-tail-4",
        "text": "do something",
        "cwd": "/tmp/proj",
        "started": True,
        "thread_id": "thread-4",
    }

    before = set(asyncio.all_tasks(loop))
    handle_host_command(frame, incoming_queue=outbound)
    new_tasks = await _new_tasks(loop, before)
    assert new_tasks, "expected handle_host_command to schedule a headless task"
    await asyncio.gather(*new_tasks, return_exceptions=True)

    assert calls == [("started", None), ("finished", True)]
    assert tail._suppress is False

    stopped_tail = wsclient_module._transcript_tails.pop("sess-tail-4")
    await stopped_tail.stop()


@pytest.mark.asyncio
async def test_headless_prompt_failure_marks_drive_finished_false(monkeypatch, fake_transcript):
    """
    GIVEN a registered tail for a claude_session_id
    WHEN a headless.prompt run for that session fails (is_error=True)
    THEN drive_finished(success=False) is invoked, so the tail's buffered
         events (the transcript fallback) would be replayed.
    """
    from agent_host.transcript_tail import TranscriptTail

    calls: list[tuple[str, bool | None]] = []

    class SpyTail(TranscriptTail):
        def drive_finished(self, success: bool) -> None:
            calls.append(("finished", success))
            super().drive_finished(success)

    loop = asyncio.get_running_loop()
    tail = SpyTail("sess-tail-5", "/tmp/proj", emit=lambda ev: None, loop=loop)
    tail.start()
    wsclient_module._transcript_tails["sess-tail-5"] = tail

    def fake_run_headless_streaming(prompt, claude_session_id, cwd, started, on_event):
        return {"text": "boom", "is_error": True}

    monkeypatch.setattr(
        "agent_host.claude_headless.run_headless_streaming", fake_run_headless_streaming
    )

    outbound: asyncio.Queue = asyncio.Queue()
    frame = {
        "type": "host_command",
        "command": "headless.prompt",
        "claude_session_id": "sess-tail-5",
        "text": "do something",
        "cwd": "/tmp/proj",
        "started": True,
        "thread_id": "thread-5",
    }

    before = set(asyncio.all_tasks(loop))
    handle_host_command(frame, incoming_queue=outbound)
    new_tasks = await _new_tasks(loop, before)
    assert new_tasks
    await asyncio.gather(*new_tasks, return_exceptions=True)

    assert ("finished", False) in calls

    stopped_tail = wsclient_module._transcript_tails.pop("sess-tail-5")
    await stopped_tail.stop()
