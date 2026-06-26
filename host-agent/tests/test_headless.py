"""Tests for agent_host.claude_headless.run_headless.

Invariants verified:
  - First turn (started=False) uses --session-id, not --resume.
  - Later turns (started=True) use --resume, not --session-id.
  - Argv always includes --output-format json and --permission-mode bypassPermissions.
  - Non-JSON stdout → is_error=True, no raise.
  - subprocess.TimeoutExpired → is_error=True, no raise.
  - Unexpected exception from subprocess.run → is_error=True, no raise.
  - Valid result JSON → {'text': '<result>', 'is_error': False}.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agent_host.claude_headless import run_headless


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    """Build a fake CompletedProcess with the given stdout."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.returncode = returncode
    return cp


# ---------------------------------------------------------------------------
# argv shape — first turn (started=False)
# ---------------------------------------------------------------------------


def test_first_turn_uses_session_id_flag():
    """
    GIVEN started=False (first turn of a headless session)
    WHEN  run_headless builds the subprocess argv
    THEN  it includes --session-id <uuid> and does NOT include --resume.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000001"
    fake_result = json.dumps({"type": "result", "is_error": False, "result": "hello", "session_id": session_id})

    with patch("subprocess.run", return_value=_make_completed(fake_result)) as mock_run:
        run_headless("hi", session_id, cwd="", started=False)

    argv = mock_run.call_args[0][0]
    assert "--session-id" in argv
    assert session_id in argv
    assert "--resume" not in argv


def test_first_turn_includes_required_flags():
    """
    GIVEN started=False
    WHEN  run_headless is called
    THEN  argv includes --output-format json and --permission-mode bypassPermissions.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000002"
    fake = json.dumps({"type": "result", "is_error": False, "result": "ok", "session_id": session_id})

    with patch("subprocess.run", return_value=_make_completed(fake)) as mock_run:
        run_headless("prompt", session_id, cwd="", started=False)

    argv = mock_run.call_args[0][0]
    assert "--output-format" in argv
    assert "json" in argv
    assert "--permission-mode" in argv
    assert "bypassPermissions" in argv


# ---------------------------------------------------------------------------
# argv shape — later turn (started=True)
# ---------------------------------------------------------------------------


def test_later_turn_uses_resume_flag():
    """
    GIVEN started=True (resuming an existing session)
    WHEN  run_headless builds the subprocess argv
    THEN  it includes --resume <uuid> and does NOT include --session-id.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000003"
    fake = json.dumps({"type": "result", "is_error": False, "result": "world", "session_id": session_id})

    with patch("subprocess.run", return_value=_make_completed(fake)) as mock_run:
        run_headless("follow-up", session_id, cwd="", started=True)

    argv = mock_run.call_args[0][0]
    assert "--resume" in argv
    assert session_id in argv
    assert "--session-id" not in argv


# ---------------------------------------------------------------------------
# Error handling — non-JSON stdout
# ---------------------------------------------------------------------------


def test_non_json_stdout_returns_is_error_and_does_not_raise():
    """
    GIVEN subprocess.run returns non-JSON stdout
    WHEN  run_headless parses the output
    THEN  it returns {'is_error': True} and does NOT raise.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000004"

    with patch("subprocess.run", return_value=_make_completed("not json at all")):
        result = run_headless("test", session_id, cwd="", started=False)

    assert result["is_error"] is True
    assert isinstance(result["text"], str)


# ---------------------------------------------------------------------------
# Error handling — TimeoutExpired
# ---------------------------------------------------------------------------


def test_timeout_returns_is_error_and_does_not_raise():
    """
    GIVEN subprocess.run raises subprocess.TimeoutExpired
    WHEN  run_headless is called
    THEN  it returns {'is_error': True} and does NOT raise.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000005"

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=600)):
        result = run_headless("test", session_id, cwd="", started=False)

    assert result["is_error"] is True
    assert "timeout" in result["text"].lower()


# ---------------------------------------------------------------------------
# Error handling — unexpected exception
# ---------------------------------------------------------------------------


def test_unexpected_exception_returns_is_error_and_does_not_raise():
    """
    GIVEN subprocess.run raises an unexpected RuntimeError
    WHEN  run_headless is called
    THEN  it returns {'is_error': True} and does NOT raise.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000006"

    with patch("subprocess.run", side_effect=RuntimeError("something broke")):
        result = run_headless("test", session_id, cwd="", started=False)

    assert result["is_error"] is True
    assert isinstance(result["text"], str)


# ---------------------------------------------------------------------------
# Happy path — valid result JSON
# ---------------------------------------------------------------------------


def test_valid_result_json_returns_text_and_not_is_error():
    """
    GIVEN subprocess.run returns a valid Claude result JSON with is_error=False
    WHEN  run_headless parses it
    THEN  it returns {'text': '<result>', 'is_error': False}.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000007"
    expected_text = "The answer is 42."
    payload = json.dumps({
        "type": "result",
        "is_error": False,
        "result": expected_text,
        "session_id": session_id,
    })

    with patch("subprocess.run", return_value=_make_completed(payload)):
        result = run_headless("what is 6*7?", session_id, cwd="", started=False)

    assert result == {"text": expected_text, "is_error": False}


def test_falls_back_to_other_session_flag_on_failure():
    """
    GIVEN started=False so the first attempt uses --session-id, and that attempt
          fails (e.g. the session already exists → non-JSON error output)
    WHEN  run_headless retries
    THEN  it falls back to --resume and returns the successful result.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000009"
    good = json.dumps({"type": "result", "is_error": False, "result": "RECOVERED", "session_id": session_id})
    # 1st call (--session-id): non-JSON error; 2nd call (--resume): success.
    with patch("subprocess.run", side_effect=[_make_completed("error: session exists"), _make_completed(good)]) as mock_run:
        result = run_headless("hi", session_id, cwd="", started=False)

    assert result == {"text": "RECOVERED", "is_error": False}
    assert mock_run.call_count == 2
    first_argv, second_argv = mock_run.call_args_list[0][0][0], mock_run.call_args_list[1][0][0]
    assert "--session-id" in first_argv and "--resume" in second_argv


def test_is_error_true_in_result_json_propagates():
    """
    GIVEN subprocess.run returns a valid Claude result JSON with is_error=True
    WHEN  run_headless parses it
    THEN  it returns {'text': '...', 'is_error': True}.
    """
    session_id = "aaaaaaaa-0000-0000-0000-000000000008"
    payload = json.dumps({
        "type": "result",
        "is_error": True,
        "result": "Claude error message",
        "session_id": session_id,
    })

    with patch("subprocess.run", return_value=_make_completed(payload)):
        result = run_headless("bad prompt", session_id, cwd="", started=False)

    assert result["is_error"] is True
    assert result["text"] == "Claude error message"


# ---------------------------------------------------------------------------
# Cross-platform binary resolution (headless-default spec)
# ---------------------------------------------------------------------------


def test_resolve_claude_bin_honors_env_override(monkeypatch):
    """
    GIVEN $ORC_CLAUDE_BIN is set
    WHEN  _resolve_claude_bin is called
    THEN  it returns that path verbatim (no PATH lookup) — cross-platform escape hatch.
    """
    from agent_host.claude_headless import _resolve_claude_bin

    monkeypatch.setenv("ORC_CLAUDE_BIN", "/custom/path/claude.exe")
    assert _resolve_claude_bin() == "/custom/path/claude.exe"


def test_resolve_claude_bin_falls_back_to_path(monkeypatch):
    """
    GIVEN no override
    WHEN  _resolve_claude_bin is called
    THEN  it uses shutil.which (finds claude / claude.cmd / claude.exe on PATH).
    """
    from agent_host import claude_headless

    monkeypatch.delenv("ORC_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(claude_headless.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert claude_headless._resolve_claude_bin() == "/usr/bin/claude"


def test_missing_binary_returns_clear_error_not_crash(monkeypatch):
    """
    GIVEN claude is not installed (exec raises FileNotFoundError)
    WHEN  run_headless is called
    THEN  it returns a clear is_error result mentioning ORC_CLAUDE_BIN — never raises,
          never exec's a hardcoded machine-specific path.
    """
    from agent_host import claude_headless

    monkeypatch.delenv("ORC_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(claude_headless.shutil, "which", lambda name: None)

    def _boom(*a, **k):
        raise FileNotFoundError("no claude")

    with patch("subprocess.run", side_effect=_boom):
        result = run_headless("hi", "aaaaaaaa-0000-0000-0000-000000000099", cwd="", started=False)

    assert result["is_error"] is True
    assert "ORC_CLAUDE_BIN" in result["text"]


# ---------------------------------------------------------------------------
# Streaming runner (stream-json → per-event relay)
# ---------------------------------------------------------------------------


def test_run_headless_streaming_emits_events(monkeypatch):
    """
    GIVEN claude -p --output-format stream-json emits init, a tool_use, an
          assistant text block, and a final result
    WHEN run_headless_streaming is called
    THEN on_event fires for the tool step ("🔧 Read") and the text, and the
         returned result carries the final text with is_error=False.
    """
    from agent_host import claude_headless

    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read"}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Here it is"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"result":"Here it is"}',
    ]

    class FakeProc:
        def __init__(self):
            self.stdout = iter([ln + "\n" for ln in lines])
            self.stderr = None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(claude_headless, "_resolve_claude_bin", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_headless.subprocess, "Popen", lambda *a, **k: FakeProc())

    events = []
    res = claude_headless.run_headless_streaming("hi", "sid-1", "/tmp", True, events.append)

    assert res["is_error"] is False
    assert res["text"] == "Here it is"
    assert "🔧 Read" in events
    assert "Here it is" in events


def test_run_headless_streaming_no_result_is_error(monkeypatch):
    """
    GIVEN the stream ends with no result event (e.g. claude crashed mid-stream)
    WHEN run_headless_streaming is called for BOTH resume and create attempts
    THEN it returns is_error=True (so the caller surfaces a failure, not silence).
    """
    from agent_host import claude_headless

    class FakeProc:
        def __init__(self):
            self.stdout = iter([])  # empty stream
            self.stderr = None

        def wait(self, timeout=None):
            return 1

        def kill(self):
            pass

    monkeypatch.setattr(claude_headless, "_resolve_claude_bin", lambda: "/usr/bin/claude")
    monkeypatch.setattr(claude_headless.subprocess, "Popen", lambda *a, **k: FakeProc())

    res = claude_headless.run_headless_streaming("hi", "sid-2", "/tmp", False, lambda t: None)
    assert res["is_error"] is True
