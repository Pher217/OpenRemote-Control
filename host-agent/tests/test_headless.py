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
