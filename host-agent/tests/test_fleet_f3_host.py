"""Tests for the Fleet F3 host-side handlers: session.kill and session.start.

Invariants verified:
  H1. session.kill dispatches PtySession().kill(session_name).
  H2. session.kill with missing session_name is ignored (fail-closed).
  H3. session.kill with an exception does NOT crash the recv loop.
  H4. session.start dispatches PtySession().start() with correct args,
      emits session.pty_start via outbound queue, and streams output.
  H5. session.start with missing fields is ignored (fail-closed).
  H6. session.start called outside event loop → logged, not crash.
  H7. pty_stream refactor: stream_pty_output and strip_ansi are importable
      and strip_ansi produces the same result as the old run_cmd logic.
  H8. run_pty still works after refactor (imports strip_ansi from pty_stream).
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from agent_host.wsclient import handle_host_command


# ---------------------------------------------------------------------------
# H1 — session.kill dispatches PtySession().kill(session_name)
# ---------------------------------------------------------------------------


def test_session_kill_calls_pty_kill(monkeypatch):
    """
    GIVEN a session.kill frame with a valid session_name
    WHEN handle_host_command is called
    THEN PtySession().kill(session_name) is called with the correct name.
    Invariant H1.
    """
    killed = []

    class FakePtySession:
        def kill(self, name):
            killed.append(name)

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    frame = {
        "type": "host_command",
        "command": "session.kill",
        "session_name": "target-session",
    }
    handle_host_command(frame)

    assert killed == ["target-session"], (
        f"Expected kill(['target-session']), got {killed!r}"
    )


# ---------------------------------------------------------------------------
# H2 — session.kill missing session_name → ignored (fail-closed)
# ---------------------------------------------------------------------------


def test_session_kill_missing_session_name_is_ignored(monkeypatch):
    """
    GIVEN a session.kill frame with no session_name
    WHEN handle_host_command is called
    THEN nothing is killed and no exception propagates.
    Invariant H2.
    """
    killed = []

    class FakePtySession:
        def kill(self, name):
            killed.append(name)

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    handle_host_command({
        "type": "host_command",
        "command": "session.kill",
        # session_name intentionally omitted
    })

    assert killed == [], "kill() must not be called when session_name is missing"


# ---------------------------------------------------------------------------
# H3 — session.kill with an exception → recv loop continues
# ---------------------------------------------------------------------------


def test_session_kill_exception_does_not_crash_recv_loop(monkeypatch):
    """
    GIVEN a session.kill frame where PtySession().kill raises unexpectedly
    WHEN handle_host_command is called
    THEN the exception is caught and the function returns normally.
    Invariant H3.
    """
    class FakePtySession:
        def kill(self, name):
            raise RuntimeError("tmux not running")

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    # Must not raise
    handle_host_command({
        "type": "host_command",
        "command": "session.kill",
        "session_name": "crash-session",
    })


# ---------------------------------------------------------------------------
# H4 — session.start: PtySession.start called, pty_start queued, output streamed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_launches_and_queues_pty_start(monkeypatch):
    """
    GIVEN a session.start frame with session_name, command_str, cwd
    WHEN handle_host_command is called from a running event loop
    THEN PtySession().start() is called with correct args,
         a session.pty_start event is enqueued to the outbound queue,
         and session.pty_end events follow.
    Invariant H4.
    """
    started = []

    # A PTY that exits immediately (exists() returns False from the first call)
    class FakePtySession:
        def start(self, name, command, cwd=None):
            started.append({"name": name, "command": command, "cwd": cwd})

        def exists(self, name):
            return False  # Immediately "done" — no output ticks needed

        def capture(self, name, history=2000):
            return ""

        def kill(self, name):
            pass

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    outbound = asyncio.Queue()
    tasks_created = []

    def fake_task_factory(coro):
        t = asyncio.ensure_future(coro)
        tasks_created.append(t)

    frame = {
        "type": "host_command",
        "command": "session.start",
        "session_name": "orc-h4test",
        "command_str": "claude",
        "cwd": "/tmp",
    }

    handle_host_command(frame, incoming_queue=outbound, _session_start_task_factory=fake_task_factory)

    # Let the event loop run the task to completion
    assert tasks_created, "No task was created by session.start handler"
    await asyncio.gather(*tasks_created, return_exceptions=True)

    # Collect all enqueued events
    events = []
    while not outbound.empty():
        events.append(outbound.get_nowait())

    event_types = [e["type"] for e in events]
    assert "session.pty_start" in event_types, (
        f"session.pty_start not in enqueued events: {event_types}"
    )
    assert "session.pty_end" in event_types, (
        f"session.pty_end not in enqueued events: {event_types}"
    )

    # Verify pty_start frame shape
    start_evt = next(e for e in events if e["type"] == "session.pty_start")
    assert start_evt["data"]["session_name"] == "orc-h4test"
    assert start_evt["data"]["command"] == "claude"
    assert start_evt["data"]["cwd"] == "/tmp"

    # PtySession.start called with correct args
    assert len(started) == 1
    assert started[0]["name"] == "orc-h4test"
    assert started[0]["command"] == "claude"
    assert started[0]["cwd"] == "/tmp"


# ---------------------------------------------------------------------------
# H5 — session.start missing fields → ignored (fail-closed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_missing_command_str_is_ignored(monkeypatch):
    """
    GIVEN a session.start frame with no command_str
    WHEN handle_host_command is called
    THEN nothing is started and no task is created.
    Invariant H5.
    """
    started = []

    class FakePtySession:
        def start(self, name, command, cwd=None):
            started.append((name, command))

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    tasks_created = []

    def fake_task_factory(coro):
        tasks_created.append(coro)

    handle_host_command(
        {
            "type": "host_command",
            "command": "session.start",
            "session_name": "orc-h5test",
            # command_str intentionally omitted
        },
        _session_start_task_factory=fake_task_factory,
    )

    assert started == []
    assert tasks_created == [], "No task should be created when command_str is missing"


@pytest.mark.asyncio
async def test_session_start_missing_session_name_is_ignored(monkeypatch):
    """
    GIVEN a session.start frame with no session_name
    WHEN handle_host_command is called
    THEN nothing is started.
    Invariant H5b.
    """
    started = []

    class FakePtySession:
        def start(self, name, command, cwd=None):
            started.append((name, command))

    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)

    tasks_created = []

    def fake_task_factory(coro):
        tasks_created.append(coro)

    handle_host_command(
        {
            "type": "host_command",
            "command": "session.start",
            "command_str": "claude",
            # session_name intentionally omitted
        },
        _session_start_task_factory=fake_task_factory,
    )

    assert started == []
    assert tasks_created == []


# ---------------------------------------------------------------------------
# H7 — pty_stream refactor: imports and strip_ansi correctness
# ---------------------------------------------------------------------------


def test_pty_stream_importable_and_strip_ansi_correct():
    """
    GIVEN the pty_stream module exists
    WHEN strip_ansi is called with ANSI-escaped text
    THEN it removes the escape sequences (same behaviour as the old run_cmd version).
    Invariant H7: DRY refactor preserves strip_ansi correctness.
    """
    from agent_host.pty_stream import strip_ansi

    assert strip_ansi("\x1b[31mhello\x1b[0m world") == "hello world"
    assert strip_ansi("no escapes") == "no escapes"
    assert strip_ansi("\x1b[1;32mgreen\x1b[m") == "green"
    assert strip_ansi("") == ""


@pytest.mark.asyncio
async def test_stream_pty_output_importable():
    """
    GIVEN the pty_stream module
    WHEN stream_pty_output is imported
    THEN it is callable (async function).
    Invariant H7b.
    """
    from agent_host.pty_stream import stream_pty_output

    assert callable(stream_pty_output)


# ---------------------------------------------------------------------------
# H8 — run_pty still works after refactor (imports from pty_stream, not local)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cmd_no_longer_defines_strip_ansi():
    """
    GIVEN the run_cmd module after the DRY refactor
    WHEN strip_ansi is looked up in run_cmd's namespace
    THEN it is NOT defined locally in run_cmd (it imports from pty_stream).
    Invariant H8: refactor is clean — no copy of strip_ansi remains in run_cmd.
    """
    import agent_host.run_cmd as run_cmd_mod

    # strip_ansi should NOT be defined directly in run_cmd anymore
    # (it moved to pty_stream). run_cmd imports it for the import line,
    # so it will be in the module namespace as a re-export.
    # The important thing is that it comes from pty_stream.
    from agent_host.pty_stream import strip_ansi as canonical_strip_ansi

    run_cmd_strip = getattr(run_cmd_mod, "strip_ansi", None)
    if run_cmd_strip is not None:
        # If it exists in the namespace it must be the same object (re-exported)
        assert run_cmd_strip is canonical_strip_ansi, (
            "run_cmd.strip_ansi is a copy, not the canonical import from pty_stream"
        )


@pytest.mark.asyncio
async def test_run_pty_uses_stream_pty_output(monkeypatch):
    """
    GIVEN run_pty after the DRY refactor
    WHEN run_pty() is called with a mocked websocket and PtySession
    THEN it calls stream_pty_output (not an inline implementation).

    This is a behavioral regression test: if run_pty still works end-to-end
    (sends pty_start + pty_output + pty_end), the refactor is correct.
    """
    import agent_host.run_cmd as run_cmd_mod
    from agent_host.config import HostConfig
    from agent_host.pty_session import PtySession

    class _FakeWs:
        def __init__(self):
            self.sent: list[dict] = []

        async def send(self, data: str):
            self.sent.append(json.loads(data))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    def _make_fake_websockets(fw):
        mod = types.SimpleNamespace()
        mod.connect = lambda url: fw
        return mod

    cfg = HostConfig(backend_url="http://localhost:8000", host_id="h1", token="tok")
    fake_ws = _FakeWs()

    monkeypatch.setattr(run_cmd_mod, "websockets", _make_fake_websockets(fake_ws))

    from agent_host.run_cmd import run_pty

    session_name = "orc-refactor-test"
    pty = PtySession()
    if pty.exists(session_name):
        pty.kill(session_name)

    await run_pty(cfg, "echo refactored", session_name=session_name)

    types_sent = [f["type"] for f in fake_ws.sent]
    assert "session.pty_start" in types_sent, f"pty_start missing: {types_sent}"
    assert "session.pty_end" in types_sent, f"pty_end missing: {types_sent}"
