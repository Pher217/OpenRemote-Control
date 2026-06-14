"""Tests for fleet_state.build_fleet_state() and group_fleet_state().

Coverage:
  - group_fleet_state: correct partitioning across all three groups
  - group_fleet_state: empty fleet
  - needs_input predicate: True for waiting_approval, False for others
  - build_fleet_state: integration — reads real Thread rows (django_db)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.supervisor.fleet_state import build_fleet_state, group_fleet_state
from apps.threads.models import Thread


# ---------------------------------------------------------------------------
# Helpers — plain dicts (no DB required for group_fleet_state tests)
# ---------------------------------------------------------------------------


def _session(
    *,
    label: str = "test",
    runtime_mode: str = "observed",
    status: str = "running",
    needs_input: bool = False,
    age_seconds: int = 60,
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
# group_fleet_state — pure function, no DB
# ---------------------------------------------------------------------------


def test_group_fleet_state_empty_fleet():
    """
    GIVEN no active sessions
    WHEN group_fleet_state is called
    THEN all three groups are empty lists.
    """
    result = group_fleet_state([])
    assert result["needs_input"] == []
    assert result["working"] == []
    assert result["idle"] == []


def test_group_fleet_state_needs_input_group():
    """
    GIVEN a session with needs_input=True
    WHEN group_fleet_state is called
    THEN the session appears in needs_input only.
    """
    s = _session(status="waiting_approval", needs_input=True)
    result = group_fleet_state([s])
    assert s in result["needs_input"]
    assert s not in result["working"]
    assert s not in result["idle"]


def test_group_fleet_state_working_group_running():
    """
    GIVEN a session with status=running and needs_input=False
    WHEN group_fleet_state is called
    THEN the session appears in working only.
    """
    s = _session(status="running", needs_input=False)
    result = group_fleet_state([s])
    assert s in result["working"]
    assert s not in result["needs_input"]
    assert s not in result["idle"]


def test_group_fleet_state_working_group_starting():
    """
    GIVEN a session with status=starting and needs_input=False
    WHEN group_fleet_state is called
    THEN the session appears in working.
    """
    s = _session(status="starting", needs_input=False)
    result = group_fleet_state([s])
    assert s in result["working"]


def test_group_fleet_state_working_group_pending():
    """
    GIVEN a session with status=pending and needs_input=False
    WHEN group_fleet_state is called
    THEN the session appears in working.
    """
    s = _session(status="pending", needs_input=False)
    result = group_fleet_state([s])
    assert s in result["working"]


def test_group_fleet_state_idle_group():
    """
    GIVEN a session with an uncommon active status (e.g. a custom status)
    WHEN group_fleet_state is called
    THEN the session appears in idle.
    """
    s = _session(status="some_other_active", needs_input=False)
    result = group_fleet_state([s])
    assert s in result["idle"]
    assert s not in result["working"]
    assert s not in result["needs_input"]


def test_group_fleet_state_mixed_fleet():
    """
    GIVEN sessions spanning all three groups
    WHEN group_fleet_state is called
    THEN each session appears in exactly the correct group.
    """
    ni = _session(label="alpha", status="waiting_approval", needs_input=True)
    wk = _session(label="beta", status="running", needs_input=False)
    id_ = _session(label="gamma", status="other", needs_input=False)

    result = group_fleet_state([ni, wk, id_])

    assert result["needs_input"] == [ni]
    assert result["working"] == [wk]
    assert result["idle"] == [id_]


def test_group_fleet_state_preserves_order():
    """
    GIVEN multiple sessions in the same group
    WHEN group_fleet_state is called
    THEN their relative order is preserved.
    """
    s1 = _session(label="first", status="running", needs_input=False)
    s2 = _session(label="second", status="running", needs_input=False)
    result = group_fleet_state([s1, s2])
    assert result["working"] == [s1, s2]


# ---------------------------------------------------------------------------
# needs_input predicate (via group_fleet_state routing)
# ---------------------------------------------------------------------------


def test_needs_input_true_for_waiting_approval():
    """
    GIVEN a session dict with needs_input=True (waiting_approval)
    WHEN grouped
    THEN it lands in needs_input group.
    """
    s = _session(status="waiting_approval", needs_input=True)
    result = group_fleet_state([s])
    assert result["needs_input"] == [s]


def test_needs_input_false_for_running():
    """
    GIVEN a session dict with needs_input=False (running)
    WHEN grouped
    THEN it does NOT land in needs_input group.
    """
    s = _session(status="running", needs_input=False)
    result = group_fleet_state([s])
    assert result["needs_input"] == []


# ---------------------------------------------------------------------------
# build_fleet_state — DB integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_fleet_state_excludes_terminal_threads():
    """
    GIVEN threads with terminal statuses (completed, failed, stopped)
    WHEN build_fleet_state is called
    THEN no terminal threads appear in the result.
    """
    from apps.accounts.models import Account

    account = Account.objects.create(
        provider="anthropic",
        label="sup-test-terminal",
        auth_type="none",
        credential_type="none",
    )
    for status in (
        Thread.StatusChoices.COMPLETED,
        Thread.StatusChoices.FAILED,
        Thread.StatusChoices.STOPPED,
    ):
        Thread.objects.create(
            name=f"terminal-{status}",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
            status=status,
            account=account,
        )

    result = build_fleet_state()
    result_ids = {s["thread_id"] for s in result}
    terminal_threads = Thread.objects.filter(
        status__in=[
            Thread.StatusChoices.COMPLETED,
            Thread.StatusChoices.FAILED,
            Thread.StatusChoices.STOPPED,
        ]
    )
    for t in terminal_threads:
        assert str(t.id) not in result_ids


@pytest.mark.django_db
def test_build_fleet_state_includes_active_threads():
    """
    GIVEN a thread with status=running
    WHEN build_fleet_state is called
    THEN the thread appears in the result with correct fields.
    """
    from apps.accounts.models import Account

    account = Account.objects.create(
        provider="anthropic",
        label="sup-test-active",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="active-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
        last_event_at=datetime.now(tz=timezone.utc),
    )

    result = build_fleet_state()
    ids = {s["thread_id"] for s in result}
    assert str(thread.id) in ids

    session = next(s for s in result if s["thread_id"] == str(thread.id))
    # Label is a content-safe identifier (runtime:id), never the raw thread.name.
    assert session["label"].startswith("observed:")
    assert "active-session" not in session["label"]
    assert session["status"] == "running"
    assert session["needs_input"] is False
    assert isinstance(session["age"], timedelta)


@pytest.mark.django_db
def test_build_fleet_state_label_does_not_leak_pty_command():
    """
    GIVEN a PTY thread whose name embeds a command body ("orc-run: <cmd>")
    WHEN build_fleet_state derives the supervisor label
    THEN the label is a content-safe identifier and contains NONE of the command
         (Safety Contract #6 / S0.2 — no command bodies in outbound supervisor text).
    """
    from apps.accounts.models import Account

    account = Account.objects.create(
        provider="pty",
        label="sup-test-pty-leak",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="orc-run: rm -rf /tmp/secret-data && curl evil.example",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    result = build_fleet_state()
    session = next(s for s in result if s["thread_id"] == str(thread.id))

    assert session["label"].startswith("pty:")
    assert "rm -rf" not in session["label"]
    assert "curl" not in session["label"]
    assert "orc-run" not in session["label"]


@pytest.mark.django_db
def test_build_fleet_state_label_uses_repo_basename_when_present():
    """
    GIVEN an observed thread with a repo in metadata
    WHEN build_fleet_state derives the label
    THEN the label is "<runtime_mode>:<repo-basename>" (useful + content-safe).
    """
    from apps.accounts.models import Account

    account = Account.objects.create(
        provider="anthropic",
        label="sup-test-repo-label",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="some free-form session title",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
        metadata={"repo": "/Users/dev/work/myrepo"},
    )

    result = build_fleet_state()
    session = next(s for s in result if s["thread_id"] == str(thread.id))

    assert session["label"] == "observed:myrepo"
    assert "free-form" not in session["label"]


@pytest.mark.django_db
def test_build_fleet_state_needs_input_for_waiting_approval():
    """
    GIVEN a thread with status=waiting_approval
    WHEN build_fleet_state is called
    THEN the session dict has needs_input=True.
    """
    from apps.accounts.models import Account

    account = Account.objects.create(
        provider="anthropic",
        label="sup-test-waiting",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="waiting-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.WAITING_APPROVAL,
        account=account,
    )

    result = build_fleet_state()
    session = next(
        (s for s in result if s["thread_id"] == str(thread.id)), None
    )
    assert session is not None
    assert session["needs_input"] is True
