"""
Tests for run_cmd.py — strip_ansi, and the run_pty ws frame contract.

Real tmux is available in this environment (tmux 3.6b installed).
The ws is mocked (fake _FakeWs) so no backend is needed.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest

from agent_host.config import HostConfig
from agent_host.pty_stream import strip_ansi
from agent_host.run_cmd import ensure_claude_session_id, run_pty

# ---------------------------------------------------------------------------
# ensure_claude_session_id tests
# ---------------------------------------------------------------------------


def test_ensure_claude_session_id_appends_for_claude():
    """
    GIVEN a bare `claude` launch command
    WHEN ensure_claude_session_id() is called
    THEN it appends `--session-id <uuid>` and returns that uuid.
    """
    cmd, sid = ensure_claude_session_id("claude")
    assert sid is not None
    assert f"--session-id {sid}" in cmd


def test_ensure_claude_session_id_handles_absolute_path():
    """
    GIVEN claude invoked by absolute path (as the orc-claude launcher does)
    WHEN ensure_claude_session_id() is called
    THEN it still recognises claude by basename and appends a session id.
    """
    cmd, sid = ensure_claude_session_id("/Users/x/.local/bin/claude")
    assert sid is not None
    assert "--session-id" in cmd


def test_ensure_claude_session_id_skips_non_claude():
    """
    GIVEN a non-claude command
    WHEN ensure_claude_session_id() is called
    THEN the command is unchanged and the id is None.
    """
    cmd, sid = ensure_claude_session_id("codex --foo")
    assert sid is None
    assert cmd == "codex --foo"


def test_ensure_claude_session_id_honours_explicit_id():
    """
    GIVEN a claude command that already specifies --session-id
    WHEN ensure_claude_session_id() is called
    THEN the explicit id is preserved and returned, not duplicated.
    """
    cmd, sid = ensure_claude_session_id("claude --session-id abc-123")
    assert sid == "abc-123"
    assert cmd.count("--session-id") == 1


def test_ensure_claude_session_id_honours_explicit_equals_form():
    """
    GIVEN a claude command using the --session-id=<id> form
    WHEN ensure_claude_session_id() is called
    THEN the explicit id is preserved, not duplicated with a second flag.
    """
    cmd, sid = ensure_claude_session_id("claude --session-id=abc-123")
    assert sid == "abc-123"
    assert cmd.count("--session-id") == 1

# ---------------------------------------------------------------------------
# strip_ansi tests
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_csi_sequences():
    """
    GIVEN strings with ANSI CSI color/style sequences
    WHEN strip_ansi() is called
    THEN all escape sequences are removed but the text content is preserved.
    """
    assert strip_ansi("\x1b[31mhello\x1b[0m world") == "hello world"
    assert strip_ansi("no escapes") == "no escapes"
    assert strip_ansi("\x1b[1;32mgreen\x1b[m") == "green"


def test_strip_ansi_leaves_plain_text_unchanged():
    """
    GIVEN a plain text string with no escape sequences
    WHEN strip_ansi() is called
    THEN the string is returned unchanged.
    """
    assert strip_ansi("hello world") == "hello world"
    assert strip_ansi("") == ""
    assert strip_ansi("line1\nline2") == "line1\nline2"


# ---------------------------------------------------------------------------
# Fake WebSocket context manager
# ---------------------------------------------------------------------------


class _FakeWs:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, data: str):
        self.sent.append(json.loads(data))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass


def _make_fake_websockets(fake_ws: _FakeWs):
    """Return a fake module whose .connect() returns fake_ws."""
    mod = types.SimpleNamespace()
    mod.connect = lambda url: fake_ws
    return mod


# ---------------------------------------------------------------------------
# run_pty integration tests (require tmux)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_pty_creates_tmux_session(monkeypatch):
    """
    GIVEN a HostConfig and a simple echo command
    WHEN run_pty() is called with a mocked websocket
    THEN: pty_start frame is sent first, pty_output frame(s) carry stripped text,
          pty_end frame is sent last, and the tmux session is gone after exit.
    """
    import agent_host.run_cmd as run_cmd_mod
    from agent_host.pty_session import PtySession

    cfg = HostConfig(backend_url="http://localhost:8000", host_id="h1", token="tok")
    fake_ws = _FakeWs()

    monkeypatch.setattr(run_cmd_mod, "websockets", _make_fake_websockets(fake_ws))

    session_name = "orc-test-echo"
    # Ensure no stale session
    pty = PtySession()
    if pty.exists(session_name):
        pty.kill(session_name)

    await run_pty(cfg, "echo hello && sleep 0.1", session_name=session_name)

    types_sent = [f["type"] for f in fake_ws.sent]
    assert types_sent[0] == "session.pty_start", f"First frame must be pty_start, got: {types_sent}"
    assert "session.pty_end" in types_sent, f"pty_end not found in frames: {types_sent}"
    assert any(f["type"] == "session.pty_output" for f in fake_ws.sent), (
        f"No pty_output frames found. All frames: {types_sent}"
    )

    # Check pty_output text has been ANSI-stripped
    output_frames = [f for f in fake_ws.sent if f["type"] == "session.pty_output"]
    for frame in output_frames:
        text = frame["data"]["text"]
        assert "\x1b[" not in text, f"ANSI escape found in output: {text!r}"

    # Tmux session should be gone after command exits
    assert not pty.exists(session_name), (
        f"Tmux session {session_name!r} still exists after command exit"
    )


@pytest.mark.asyncio
async def test_run_pty_frame_shapes(monkeypatch):
    """
    GIVEN a HostConfig and echo command
    WHEN run_pty() is called with a mocked websocket
    THEN the pty_start frame has the correct JSON shape.
    """
    import agent_host.run_cmd as run_cmd_mod
    from agent_host.pty_session import PtySession

    cfg = HostConfig(backend_url="http://localhost:8000", host_id="h1", token="tok")
    fake_ws = _FakeWs()

    monkeypatch.setattr(run_cmd_mod, "websockets", _make_fake_websockets(fake_ws))

    session_name = "orc-frame-shape"
    pty = PtySession()
    if pty.exists(session_name):
        pty.kill(session_name)

    await run_pty(cfg, "echo shapes", session_name=session_name, cwd="/tmp")

    # Find the pty_start frame
    start_frames = [f for f in fake_ws.sent if f["type"] == "session.pty_start"]
    assert len(start_frames) >= 1, "No pty_start frame found"
    frame = start_frames[0]

    assert frame["type"] == "session.pty_start"
    assert isinstance(frame["data"]["session_name"], str)
    assert frame["data"]["session_name"] == session_name
    assert frame["data"]["command"] == "echo shapes"
    assert isinstance(frame["data"]["cwd"], str)
    assert frame["data"]["cwd"] == "/tmp"


@pytest.mark.asyncio
async def test_run_pty_multiscreen_output_delivered(monkeypatch):
    """
    GIVEN a command that prints more lines than a terminal screen height (60 lines)
    WHEN run_pty() captures and streams via the line-based diff
    THEN the concatenated pty_output frames contain BOTH an early line (line1)
         AND a late line (line60), proving that multi-screen scrollback is captured
         and not silently dropped by the character-slice bug.

    This is a regression test for the old ``content[len(last_content):]`` diff:
    capture-pane without -S only returns the visible screen, so as output scrolled
    past the terminal height, the snapshot was no longer a superset of last_content
    and the diff shipped wrong fragments.  With -S -2000 the capture is
    append-only so all 60 lines are delivered correctly.
    """
    import agent_host.run_cmd as run_cmd_mod
    from agent_host.pty_session import PtySession

    cfg = HostConfig(backend_url="http://localhost:8000", host_id="h1", token="tok")
    fake_ws = _FakeWs()

    monkeypatch.setattr(run_cmd_mod, "websockets", _make_fake_websockets(fake_ws))

    session_name = "orc-test-multiscreen"
    pty = PtySession()
    if pty.exists(session_name):
        pty.kill(session_name)

    # Print 60 lines then pause briefly so the session stays alive long enough
    # for at least one capture tick to observe the output before tmux exits.
    command = "bash -c 'for i in $(seq 1 60); do echo line$i; done; sleep 2'"
    await run_pty(cfg, command, session_name=session_name)

    output_frames = [f for f in fake_ws.sent if f["type"] == "session.pty_output"]
    assert output_frames, "No pty_output frames delivered at all"

    full_output = "\n".join(f["data"]["text"] for f in output_frames)
    assert "line1" in full_output, (
        f"Early output 'line1' missing from streamed text.\nFull output:\n{full_output}"
    )
    assert "line60" in full_output, (
        f"Late output 'line60' missing from streamed text — multi-screen output was dropped.\n"
        f"Full output:\n{full_output}"
    )


@pytest.mark.asyncio
async def test_run_pty_line_diff_rebaseline_on_shrink(monkeypatch):
    """
    GIVEN the internal _send_diff closure sees a capture that is shorter than
         sent_lines (simulating a pane clear / history flush)
    WHEN _send_diff is called
    THEN it re-baselines sent_lines to the new length and does NOT re-ship content
         (no pty_output frame is emitted for that tick).

    This exercises the conservative re-baseline branch directly without tmux.

    NOTE: We patch PtySession via the *run_cmd* module's name-binding
    (``monkeypatch.setattr(run_cmd_mod, "PtySession", ...)``) rather than
    directly on the class from pty_session.  This is necessary because
    test_input_policy.py calls importlib.reload(agent_host.pty_session),
    which creates a new class object.  After that reload, the name bound in
    run_cmd (``from agent_host.pty_session import PtySession``) still refers
    to the pre-reload class, but a fresh ``from agent_host.pty_session import
    PtySession`` inside this test would get the post-reload class.  Patching
    the module-level name in run_cmd guarantees we patch the right object
    regardless of reload order.
    """
    import agent_host.run_cmd as run_cmd_mod

    cfg = HostConfig(backend_url="http://localhost:8000", host_id="h1", token="tok")
    fake_ws = _FakeWs()

    monkeypatch.setattr(run_cmd_mod, "websockets", _make_fake_websockets(fake_ws))

    session_name = "orc-test-rebaseline"

    # We'll intercept capture() to feed controlled snapshots.
    # Sequence: 5 lines → then "shrink" to 2 lines → then 3 lines appended.
    captures = [
        "line1\nline2\nline3\nline4\nline5",   # tick 1: 5 lines shipped
        "line1\nline2",                          # tick 2: shrink — re-baseline, no output
        "line1\nline2\nlineA\nlineB\nlineC",    # tick 3: 3 new lines shipped
        # Empty string causes fake_exists to return False → loop exits.
    ]
    capture_iter = iter(captures)
    tick_count = 0

    class FakePtySession:
        """Synthetic PTY that feeds controlled snapshots without touching tmux."""

        def start(self, name, command, cwd=None):
            pass

        def kill(self, name):
            pass

        def exists(self, name):
            return tick_count < len(captures)

        def capture(self, name, history=2000):
            return next(capture_iter, "")

    # Replace the PtySession name in run_cmd's module namespace so that
    # PtySession() inside run_pty() returns a FakePtySession instance.
    monkeypatch.setattr(run_cmd_mod, "PtySession", FakePtySession)

    # Patch asyncio.sleep to advance tick_count instead of actually sleeping.
    async def fake_sleep(delay):
        nonlocal tick_count
        tick_count += 1

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await run_pty(cfg, "synthetic", session_name=session_name)

    output_frames = [f for f in fake_ws.sent if f["type"] == "session.pty_output"]
    all_shipped = "\n".join(f["data"]["text"] for f in output_frames)

    # Lines from tick 1 should be present.
    assert "line1" in all_shipped, f"line1 missing; shipped:\n{all_shipped}"
    assert "line5" in all_shipped, f"line5 missing; shipped:\n{all_shipped}"

    # Lines from tick 3 (after re-baseline) should be present.
    assert "lineA" in all_shipped, f"lineA missing after re-baseline; shipped:\n{all_shipped}"
    assert "lineC" in all_shipped, f"lineC missing after re-baseline; shipped:\n{all_shipped}"

    # The shrink tick (tick 2) must NOT have re-shipped line1/line2 a second time.
    # Count occurrences of "line1" — should be exactly 1 (from tick 1 only).
    assert all_shipped.count("line1") == 1, (
        f"line1 appears more than once — shrink tick re-shipped content:\n{all_shipped}"
    )
