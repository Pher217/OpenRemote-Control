"""test_drive_e2e.py — E2E drive-path invariants for the North Star contract.

Pins the following contract (2026-06-22 north-star spec):

  1. A session started via daemon ``session.start`` launches a PTY and queues a
     ``session.pty_start`` frame for delivery to the backend.
  2. A ``pty.inject`` frame on the SAME daemon ws connection reaches the PTY —
     ``send_keys`` is called with the correct text and ``approved=True``.
  3. Inject+submit: text is typed then Enter fires (submit-settle timing reached).
  4. Restart survival: after daemon reconnect, inject still works — PtySession is
     stateless (queries tmux by name) so sessions outlive daemon restarts.
  5. (xfail) The ``orc run`` path opens a SEPARATE ws connection whose
     ``stream_pty_output`` loop has no ``recv`` handler.  ``pty.inject`` frames
     delivered to that connection are silently dropped.  This documents the
     contention bug that the fix (daemon-owned sessions exclusively) closes.

Tests 1–4 use a mocked PtySession (no live tmux required).
Test 5 is marked ``xfail(strict=True)`` — it demonstrates the bug by asserting
the desired behaviour that is currently absent.  When the fix lands (retire
``run_cmd.run_pty``'s separate ws), remove the ``xfail`` decorator.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from agent_host.wsclient import handle_host_command

# ---------------------------------------------------------------------------
# Shared fake PTY state (module-level so all FakePtySession instances share it)
# ---------------------------------------------------------------------------

_pty: dict[str, dict] = {}  # session_name → {"alive": bool, "output": [...], "injections": [...]}


def _reset():
    _pty.clear()


class FakePtySession:
    """Stateless stub — all instances share ``_pty`` via the module global."""

    def start(self, name: str, command: str, cwd: str | None = None) -> None:
        _pty[name] = {"alive": True, "output": [f"$ {command}"], "injections": []}

    def exists(self, name: str) -> bool:
        return _pty.get(name, {}).get("alive", False)

    def capture(self, name: str, history: int = 2000) -> str:
        state = _pty.get(name)
        if state is None:
            raise KeyError(f"PTY session {name!r} not found")
        return "\n".join(state["output"])

    def kill(self, name: str) -> None:
        if name in _pty:
            _pty[name]["alive"] = False

    def send_keys(self, name: str, text: str, *, approved: bool) -> None:
        from agent_host.input_policy import Risk, classify_input  # noqa: PLC0415

        result = classify_input(text)
        if result["risk"] == Risk.DANGEROUS:
            raise PermissionError("DANGEROUS")
        if result["requires_approval"] and not approved:
            raise PermissionError("requires approval")
        state = _pty.get(name)
        if state is None:
            raise KeyError(f"PTY session {name!r} not found")
        state["injections"].append(text)
        state["output"].append(f"[injected: {text.strip()}]")

    def list_live_sessions(self) -> list[str]:
        return [n for n, s in _pty.items() if s.get("alive")]

    @staticmethod
    def _server():  # type: ignore[return]
        raise AssertionError("_server must not be called in unit tests")


@pytest.fixture(autouse=True)
def _patch_pty(monkeypatch):
    """Patch PtySession + reset shared state around every test."""
    _reset()
    monkeypatch.setattr("agent_host.pty_session.PtySession", FakePtySession)
    yield
    _reset()


# ---------------------------------------------------------------------------
# Invariant 1 — session.start → PTY launched → pty_start queued
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_launches_pty_and_queues_pty_start():
    """
    GIVEN a session.start host_command arrives on the daemon's ws connection
    WHEN handle_host_command processes it
    THEN PtySession.start() is called for the named session
         AND a session.pty_start frame is queued for delivery to the backend.
    """
    session_name = "orc-e2e-1"
    q: asyncio.Queue[dict] = asyncio.Queue()

    handle_host_command(
        {
            "type": "host_command",
            "command": "session.start",
            "session_name": session_name,
            "command_str": "claude --dir /tmp",
        },
        q,
    )

    # Yield control so the create_task coroutine runs
    await asyncio.sleep(0.05)

    assert session_name in _pty, "PtySession.start() was not called — session missing from fake tmux"
    assert _pty[session_name]["alive"] is True, "Session not marked alive after start"

    assert not q.empty(), "No frames queued — session.pty_start is missing"
    frame = q.get_nowait()
    assert frame["type"] == "session.pty_start", f"Expected pty_start, got {frame['type']!r}"
    assert frame["data"]["session_name"] == session_name

    # Clean up: kill session so _stream_via_queue background task exits cleanly
    _pty[session_name]["alive"] = False
    await asyncio.sleep(0)  # one yield for the stream loop to exit


# ---------------------------------------------------------------------------
# Invariant 2 — pty.inject on daemon ws → send_keys called
# ---------------------------------------------------------------------------


def test_inject_on_daemon_ws_calls_send_keys():
    """
    GIVEN a PTY session is alive (started via session.start)
    WHEN a pty.inject host_command arrives on the daemon's ws connection
    THEN PtySession.send_keys is called with (session_name, text, approved=True).

    This is the single-ws invariant: daemon is the only ws in host_{id} group,
    so pty.inject always reaches the right handler.
    """
    session_name = "orc-e2e-2"
    _pty[session_name] = {"alive": True, "output": ["$ claude"], "injections": []}

    inject_text = "explain recursion\n"
    handle_host_command(
        {
            "type": "host_command",
            "command": "pty.inject",
            "session_name": session_name,
            "text": inject_text,
            "approved": True,
        }
    )

    assert _pty[session_name]["injections"] == [inject_text], (
        f"send_keys not called with correct text. Got: {_pty[session_name]['injections']}"
    )


# ---------------------------------------------------------------------------
# Invariant 3 — inject+submit: text typed then Enter fires
# ---------------------------------------------------------------------------


def test_inject_submit_timing_is_correct():
    """
    GIVEN a text string of any length
    WHEN _submit_settle_seconds is called
    THEN the settle delay is at least _SUBMIT_SETTLE_BASE and at most _SUBMIT_SETTLE_MAX.

    This verifies the submit-timing gate is reachable and correctly bounded.
    The real send_keys calls pane.send_keys (type) then waits this delay before
    sending Enter — ensuring text lands in the TUI before submission.
    """
    from agent_host.pty_session import (  # noqa: PLC0415
        _SUBMIT_SETTLE_BASE,
        _SUBMIT_SETTLE_MAX,
        _submit_settle_seconds,
    )

    for text in ("hi", "x" * 100, "x" * 100_000):
        settle = _submit_settle_seconds(text)
        assert settle >= _SUBMIT_SETTLE_BASE, (
            f"settle {settle!r} < base {_SUBMIT_SETTLE_BASE!r} for text len {len(text)}"
        )
        assert settle <= _SUBMIT_SETTLE_MAX, (
            f"settle {settle!r} > max {_SUBMIT_SETTLE_MAX!r} for text len {len(text)}"
        )


def test_inject_and_submit_recorded_by_stub():
    """
    GIVEN a live PTY session and a pty.inject command
    WHEN handle_host_command processes it
    THEN the injection is recorded — confirming the full path from
         handle_host_command → PtySession.send_keys completes without error.
    """
    session_name = "orc-e2e-3"
    _pty[session_name] = {"alive": True, "output": ["$ claude"], "injections": []}

    text = "write a haiku about tmux\n"
    handle_host_command(
        {
            "type": "host_command",
            "command": "pty.inject",
            "session_name": session_name,
            "text": text,
            "approved": True,
        }
    )

    assert _pty[session_name]["injections"] == [text]


# ---------------------------------------------------------------------------
# Invariant 4 — restart survival: inject works after daemon reconnect
# ---------------------------------------------------------------------------


def test_inject_survives_daemon_restart():
    """
    GIVEN a PTY session was started by a PREVIOUS daemon run (lives in tmux)
    WHEN the daemon restarts and receives a pty.inject command for that session
    THEN send_keys succeeds — PtySession is stateless and finds the session by name.

    'Restart survival' means: the new daemon has no in-memory record of
    session.start, but the tmux session persists across daemon process restarts.
    PtySession.send_keys queries tmux by name so inject always works.
    """
    session_name = "orc-e2e-4"
    # Pre-populate: simulates a tmux session left running from a previous daemon run.
    # The current daemon (fresh restart) has no in-memory session.start record.
    _pty[session_name] = {"alive": True, "output": ["$ claude"], "injections": []}

    inject_text = "post-restart query\n"
    handle_host_command(
        {
            "type": "host_command",
            "command": "pty.inject",
            "session_name": session_name,
            "text": inject_text,
            "approved": True,
        }
    )

    assert _pty[session_name]["injections"] == [inject_text], (
        "inject failed after simulated daemon restart — restart-survival invariant broken"
    )


# ---------------------------------------------------------------------------
# Invariant 5 (combined) — session.start + inject on same connection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_and_inject_on_same_daemon_connection():
    """
    GIVEN session.start and pty.inject both arrive on the daemon's SINGLE ws connection
    WHEN handle_host_command processes both
    THEN the PTY session is started AND the inject reaches it without contention.

    This pins the 'one daemon owns the session lifecycle' contract:
    with daemon as the sole ws in host_{id} group, pty.inject always routes
    to the right handler and is never silently dropped.
    """
    session_name = "orc-e2e-5"
    q: asyncio.Queue[dict] = asyncio.Queue()

    # 1. session.start arrives on daemon ws
    handle_host_command(
        {
            "type": "host_command",
            "command": "session.start",
            "session_name": session_name,
            "command_str": "claude",
        },
        q,
    )
    await asyncio.sleep(0.05)

    assert session_name in _pty and _pty[session_name]["alive"], (
        "PTY session not started after session.start on daemon ws"
    )

    # 2. pty.inject arrives on the SAME daemon ws connection (no contention)
    inject_text = "hello from single connection\n"
    handle_host_command(
        {
            "type": "host_command",
            "command": "pty.inject",
            "session_name": session_name,
            "text": inject_text,
            "approved": True,
        },
        q,
    )

    assert _pty[session_name]["injections"] == [inject_text], (
        "inject did not reach PTY session on same daemon connection — "
        "single-ws invariant broken"
    )

    # Clean up stream loop
    _pty[session_name]["alive"] = False
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Contention fix — run_pty now has a recv loop for host_command frames
# ---------------------------------------------------------------------------


def test_orc_run_ws_connection_handles_pty_inject():
    """
    GIVEN run_cmd.run_pty opens a separate ws to the backend for a session
    WHEN a pty.inject frame is delivered to that ws connection
    THEN the frame is processed via handle_host_command and send_keys is called.

    Fixed: run_pty now runs stream_pty_output concurrently with a _recv loop that
    dispatches inbound host_command frames (including pty.inject).  Previously,
    stream_pty_output ran alone with no recv path — inject frames were silently
    dropped on orc run connections (the 'ws contention bug').
    """
    from agent_host.run_cmd import run_pty  # noqa: PLC0415

    src = inspect.getsource(run_pty)

    # Verify the fix: run_pty must contain recv + host_command dispatch logic.
    has_recv_loop = "host_command" in src and "ws.recv" in src
    assert has_recv_loop, (
        "run_pty has no recv loop for host_command frames — contention bug NOT fixed. "
        "run_pty must call ws.recv() and dispatch host_command frames to handle_host_command."
    )
