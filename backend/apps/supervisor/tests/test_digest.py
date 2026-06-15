"""Tests for supervisor digest rendering.

Coverage:
  - render_digest: empty fleet → sentinel string
  - render_digest: grouping order (needs_input first, then working, then idle)
  - render_digest: single-session formatting (label · runtime_mode · status · age)
  - render_digest: needs_input marker present for waiting_approval session
  - render_digest: secret in label/status is redacted before output
"""

from __future__ import annotations

from datetime import timedelta

from apps.supervisor.digest import render_digest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(
    *,
    label: str = "test-session",
    runtime_mode: str = "observed",
    status: str = "running",
    needs_input: bool = False,
    age_seconds: int = 90,
) -> dict:
    return {
        "thread_id": "00000000-0000-0000-0000-000000000001",
        "label": label,
        "runtime_mode": runtime_mode,
        "host": "local",
        "status": status,
        "last_event_at": None,
        "age": timedelta(seconds=age_seconds),
        "needs_input": needs_input,
    }


# ---------------------------------------------------------------------------
# render_digest — pure function tests (no DB)
# ---------------------------------------------------------------------------


def test_render_digest_empty_fleet():
    """
    GIVEN an empty fleet state list
    WHEN render_digest is called
    THEN the output is the empty-fleet sentinel string.
    """
    result = render_digest([])
    assert result == "No active sessions."


def test_render_digest_contains_session_label():
    """
    GIVEN a single running session named 'my-project'
    WHEN render_digest is called
    THEN the label appears in the output.
    """
    s = _session(label="my-project", status="running")
    result = render_digest([s])
    assert "my-project" in result


def test_render_digest_contains_runtime_mode():
    """
    GIVEN a session with runtime_mode='pty'
    WHEN render_digest is called
    THEN 'pty' appears in the output.
    """
    s = _session(runtime_mode="pty", status="running")
    result = render_digest([s])
    assert "pty" in result


def test_render_digest_contains_age():
    """
    GIVEN a session with age of 90 seconds (1m30s → rounds to 1m)
    WHEN render_digest is called
    THEN a human-readable age appears in the output.
    """
    s = _session(age_seconds=90)  # 1m30s → "1m"
    result = render_digest([s])
    assert "1m" in result


def test_render_digest_needs_input_marker_present():
    """
    GIVEN a session with needs_input=True
    WHEN render_digest is called
    THEN the '⚠ needs input' marker appears in the session line.
    """
    s = _session(status="waiting_approval", needs_input=True)
    result = render_digest([s])
    assert "⚠ needs input" in result


def test_render_digest_no_needs_input_marker_for_running():
    """
    GIVEN a session with needs_input=False (running)
    WHEN render_digest is called
    THEN no '⚠ needs input' marker appears for that session.
    """
    s = _session(status="running", needs_input=False, label="clean-session")
    result = render_digest([s])
    # The working-group header contains ✅ but not the needs-input marker
    assert "⚠ needs input" not in result


def test_render_digest_grouping_needs_input_before_working():
    """
    GIVEN one session needing input and one running session
    WHEN render_digest is called
    THEN the 'Needs input' section header appears before 'Working'.
    """
    ni = _session(label="alpha", status="waiting_approval", needs_input=True)
    wk = _session(label="beta", status="running", needs_input=False)
    result = render_digest([ni, wk])

    pos_needs = result.find("Needs input")
    pos_working = result.find("Working")
    assert pos_needs < pos_working, (
        "Needs input section must precede Working section"
    )


def test_render_digest_working_before_idle():
    """
    GIVEN one running session and one idle session
    WHEN render_digest is called
    THEN the 'Working' section header appears before 'Idle'.
    """
    wk = _session(label="beta", status="running", needs_input=False)
    id_ = _session(label="gamma", status="other_active", needs_input=False)
    result = render_digest([wk, id_])

    pos_working = result.find("Working")
    pos_idle = result.find("Idle")
    assert pos_working < pos_idle, "Working section must precede Idle section"


def test_render_digest_redacts_openai_key_in_label():
    """
    GIVEN a session whose label contains an OpenAI API key
    WHEN render_digest is called
    THEN the key is replaced with [REDACTED] in the output.
    """
    secret = "sk-" + "A" * 24  # 24-char suffix — matches the redact pattern
    s = _session(label=f"project-{secret}", status="running")
    result = render_digest([s])
    assert secret not in result
    assert "[REDACTED]" in result


def test_render_digest_redacts_anthropic_key_in_status():
    """
    GIVEN a session whose label contains an Anthropic API key
    WHEN render_digest is called
    THEN the key does not appear in the digest output.
    """
    secret = "sk-ant-api03-" + "B" * 30
    s = _session(label=f"worker {secret}", status="running")
    result = render_digest([s])
    assert secret not in result
    assert "[REDACTED]" in result


def test_render_digest_count_in_header():
    """
    GIVEN two active sessions
    WHEN render_digest is called
    THEN the header line mentions the session count.
    """
    sessions = [
        _session(label="s1", status="running"),
        _session(label="s2", status="running"),
    ]
    result = render_digest(sessions)
    assert "2" in result


def test_render_digest_age_hours():
    """
    GIVEN a session with an age of 7200 seconds (2 hours)
    WHEN render_digest is called
    THEN '2h' appears in the output.
    """
    s = _session(age_seconds=7200)
    result = render_digest([s])
    assert "2h" in result
