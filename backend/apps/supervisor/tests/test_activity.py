"""Tests for supervisor activity.render_fleet_with_activity().

Coverage:
  - Recent turns: last MAX_TURNS turns appear, older ones do not.
  - Truncation: each turn is capped at MAX_CHARS_PER_TURN.
  - SECURITY: raw_content_encrypted is NEVER read or returned.
  - SECURITY: redact() strips secrets from redacted_content (defense-in-depth).
  - Empty fleet → "No active sessions." (delegates to render_digest).
  - Chat-surface thread is excluded (inherited from build_fleet_state).
  - render_digest is not modified (its output still embeds in the result).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from apps.supervisor.activity import (
    MAX_CHARS_PER_TURN,
    MAX_TURNS_PER_SESSION,
    render_fleet_with_activity,
)


# ---------------------------------------------------------------------------
# Shared helper — plain SessionDict for pure tests
# ---------------------------------------------------------------------------


def _session_dict(
    thread_id: str = "00000000-0000-0000-0000-000000000001",
    label: str = "test-session",
    status: str = "running",
) -> dict:
    return {
        "thread_id": thread_id,
        "label": label,
        "runtime_mode": "pty",
        "host": "local",
        "status": status,
        "last_event_at": None,
        "age": timedelta(seconds=120),
        "needs_input": False,
    }


# ---------------------------------------------------------------------------
# Pure tests (no DB — pass fleet_state directly to avoid build_fleet_state)
# ---------------------------------------------------------------------------


def test_empty_fleet_returns_no_active_sessions():
    """
    GIVEN an empty fleet state
    WHEN render_fleet_with_activity is called
    THEN the output is 'No active sessions.' (delegates to render_digest).
    """
    result = render_fleet_with_activity(fleet_state=[])
    assert result == "No active sessions."


@pytest.mark.django_db
def test_single_session_digest_line_present():
    """
    GIVEN a fleet with one running session and no Messages in the DB
    WHEN render_fleet_with_activity is called
    THEN the session label appears in the output (from render_digest).
    """
    result = render_fleet_with_activity(fleet_state=[_session_dict(label="my-project")])
    assert "my-project" in result
    assert "Fleet digest" in result


# ---------------------------------------------------------------------------
# DB tests — require django_db for Message rows
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_recent_turns_included_and_older_excluded():
    """
    GIVEN a session with more than MAX_TURNS_PER_SESSION Messages
    WHEN render_fleet_with_activity is called
    THEN only the last MAX_TURNS turns appear; earlier ones do not.
    """
    from apps.accounts.models import Account
    from apps.threads.models import Message, Thread

    account = Account.objects.create(
        provider="anthropic",
        label="activity-test-turns",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="erp-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    # Create MAX_TURNS_PER_SESSION + 2 messages so there are clearly "older" ones.
    total_messages = MAX_TURNS_PER_SESSION + 2
    for i in range(total_messages):
        Message.objects.create(
            thread=thread,
            role="assistant",
            redacted_content=f"turn-content-{i}",
            raw_content_encrypted=None,
            sequence=i,
        )

    fleet = [_session_dict(thread_id=str(thread.id), label="erp-session")]
    result = render_fleet_with_activity(fleet_state=fleet)

    # The LAST MAX_TURNS_PER_SESSION turns have indices (total-2, total-1).
    for i in range(total_messages - MAX_TURNS_PER_SESSION, total_messages):
        assert f"turn-content-{i}" in result, (
            f"Expected turn-content-{i} to appear in output (is one of last {MAX_TURNS_PER_SESSION})"
        )

    # The OLDER turns (indices 0 to total-MAX_TURNS-1) must NOT appear.
    for i in range(total_messages - MAX_TURNS_PER_SESSION):
        assert f"turn-content-{i}" not in result, (
            f"turn-content-{i} appeared in output but is older than MAX_TURNS_PER_SESSION"
        )


@pytest.mark.django_db
def test_turn_content_truncated_to_max_chars():
    """
    GIVEN a Message whose redacted_content exceeds MAX_CHARS_PER_TURN
    WHEN render_fleet_with_activity is called
    THEN the turn appears truncated (not at full length) in the output.
    """
    from apps.accounts.models import Account
    from apps.threads.models import Message, Thread

    account = Account.objects.create(
        provider="anthropic",
        label="activity-test-trunc",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="long-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    # Content much longer than MAX_CHARS_PER_TURN.  Use a repeating sentence so
    # no segment triggers the 40+ char alnum redaction patterns (which would
    # replace content before truncation — defeating the test).
    word = "hello world, this is a test sentence. "
    long_content = word * (MAX_CHARS_PER_TURN * 3 // len(word) + 1)
    assert len(long_content) > MAX_CHARS_PER_TURN  # sanity check

    Message.objects.create(
        thread=thread,
        role="assistant",
        redacted_content=long_content,
        raw_content_encrypted=None,
        sequence=0,
    )

    fleet = [_session_dict(thread_id=str(thread.id), label="long-session")]
    result = render_fleet_with_activity(fleet_state=fleet)

    # The full content must NOT appear verbatim.
    assert long_content not in result
    # Truncation marker must appear.
    assert "…" in result
    # The start of the content should be present (first few chars, well within cap).
    assert long_content[:50] in result


# ---------------------------------------------------------------------------
# SECURITY TESTS (required)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_security_raw_content_encrypted_never_appears_in_output():
    """
    GIVEN a Message with a known value in raw_content_encrypted and a
          different value in redacted_content
    WHEN render_fleet_with_activity is called
    THEN the raw_content_encrypted value NEVER appears in the output,
         confirming only redacted_content is read.

    This test directly verifies Safety Contract #6 / S0.2:
    'NEVER read raw_content_encrypted or any raw/decrypted field.'
    """
    from apps.accounts.models import Account
    from apps.threads.models import Message, Thread

    account = Account.objects.create(
        provider="anthropic",
        label="activity-test-security",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="secure-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    # Sentinel stored in the encrypted field — must NEVER appear in output.
    raw_sentinel = "RAW_ENCRYPTED_SENTINEL_VALUE_NEVER_OUTPUT"
    # Safe value stored in redacted_content — this may appear.
    safe_content = "this is safe redacted content"

    Message.objects.create(
        thread=thread,
        role="user",
        redacted_content=safe_content,
        raw_content_encrypted=raw_sentinel.encode(),
        sequence=0,
    )

    fleet = [_session_dict(thread_id=str(thread.id), label="secure-session")]
    result = render_fleet_with_activity(fleet_state=fleet)

    # The raw sentinel must NEVER appear.
    assert raw_sentinel not in result, (
        "raw_content_encrypted value leaked into render_fleet_with_activity output — "
        "Safety Contract #6 / S0.2 violated"
    )
    # The safe redacted_content may appear (confirming it IS being read).
    assert safe_content in result


@pytest.mark.django_db
def test_security_redact_strips_secrets_from_redacted_content():
    """
    GIVEN a Message whose redacted_content contains a fake secret
          (simulating a future regression where secret-stripping at write
          time fails)
    WHEN render_fleet_with_activity is called
    THEN the redact() defense-in-depth pass strips the secret from output.

    This test verifies the defense-in-depth layer described in S0.2.
    """
    from apps.accounts.models import Account
    from apps.threads.models import Message, Thread

    account = Account.objects.create(
        provider="anthropic",
        label="activity-test-redact-di",
        auth_type="none",
        credential_type="none",
    )
    thread = Thread.objects.create(
        name="redact-di-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    # A fake OpenAI key that slipped through the write-time redaction.
    fake_secret = "sk-" + "A" * 24  # matches redact()'s OpenAI key pattern

    Message.objects.create(
        thread=thread,
        role="assistant",
        redacted_content=f"Here is the key: {fake_secret}",
        raw_content_encrypted=None,
        sequence=0,
    )

    fleet = [_session_dict(thread_id=str(thread.id), label="redact-di-session")]
    result = render_fleet_with_activity(fleet_state=fleet)

    assert fake_secret not in result, (
        "Defense-in-depth redact() pass failed to strip a fake secret from "
        "redacted_content — check that _recent_turns() calls redact() on each turn"
    )
    assert "[REDACTED]" in result


# ---------------------------------------------------------------------------
# Chat-surface exclusion (inherited from build_fleet_state)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_chat_surface_thread_excluded_from_fleet():
    """
    GIVEN a TelegramChat thread alongside a real coding session
    WHEN render_fleet_with_activity is called WITHOUT a pre-built fleet_state
         (so it calls build_fleet_state internally)
    THEN the chat surface thread does NOT appear in the output.
    """
    from apps.accounts.models import Account
    from apps.telegram.models import TelegramChat
    from apps.threads.models import Thread

    account = Account.objects.create(
        provider="ollama",
        label="activity-test-chat-excl",
        auth_type="none",
        credential_type="none",
    )
    chat_thread = Thread.objects.create(
        name="telegram:123456",
        runtime="ollama",
        runtime_mode=Thread.RuntimeModeChoices.API,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )
    TelegramChat.objects.create(chat_id=123456, thread=chat_thread)

    coding = Thread.objects.create(
        name="real-coding-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        status=Thread.StatusChoices.RUNNING,
        account=account,
    )

    # Call without pre-built fleet so build_fleet_state() runs.
    result = render_fleet_with_activity()

    assert "telegram:123456" not in result
    assert "real-coding-session" in result
