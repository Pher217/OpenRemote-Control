"""Tests for Phase 1 forum-reply inbound routing.

Tests cover:
- Allowed user + matching read-only thread → read-only reply.
- No matching thread → "no matching session" reply.
- Unauthed sender → silent drop (no send).
- Wrong forum_chat_id → silent drop (no send).
- Topic-id collision across forums → correct thread returned by lookup.
- delivery._save_topic_id now also stores telegram_forum_chat_id.
"""

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.hosts.models import Host
from apps.telegram.service import handle_forum_reply
from apps.threads.models import Thread


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_account(suffix):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-fr-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-fr-{suffix}",
        credential_recipient=f"r-fr-{suffix}",
    )


def _make_host(slug):
    return Host.objects.create(slug=slug, name=slug, os="linux")


def _make_thread(account, *, runtime_mode, topic_id, forum_chat_id, host=None, tmux=None):
    meta = {
        "telegram_topic_id": topic_id,
        "telegram_forum_chat_id": forum_chat_id,
    }
    if tmux:
        meta["tmux_session_name"] = tmux
    return Thread.objects.create(
        name=f"fr-thread-{topic_id}-{forum_chat_id}",
        runtime="claude_code",
        runtime_mode=runtime_mode,
        account=account,
        host=host,
        metadata=meta,
    )


async def _make_send():
    """Return (send callable, calls list)."""
    calls = []

    async def send(chat_id, text, message_thread_id=None, reply_markup=None):
        calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "message_thread_id": message_thread_id,
                "reply_markup": reply_markup,
            }
        )

    return send, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_read_only_thread_sends_read_only_message(settings):
    """
    GIVEN an allowed user replies in a forum topic that maps to a read-only
          (OBSERVED) thread
    WHEN  handle_forum_reply is called
    THEN  a read-only reply is sent into that topic; no injection attempted.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    account = await database_sync_to_async(_make_account)("ro1")
    await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        topic_id=42,
        forum_chat_id=-100111,
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 42, 111, "hello", send=send)

    assert len(calls) == 1
    assert calls[0]["message_thread_id"] == 42
    assert "read-only" in calls[0]["text"].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_no_thread_sends_not_found(settings):
    """
    GIVEN an allowed user replies in a forum topic that maps to no thread
    WHEN  handle_forum_reply is called
    THEN  a "no matching session" reply is sent into that topic.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 9999, 111, "hello", send=send)

    assert len(calls) == 1
    assert calls[0]["message_thread_id"] == 9999
    assert "no matching session" in calls[0]["text"].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_unauthed_sender_is_silent(settings):
    """
    GIVEN a user whose id is NOT in TELEGRAM_ALLOWED_CHAT_IDS replies in a topic
    WHEN  handle_forum_reply is called
    THEN  nothing is sent (silent drop).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    account = await database_sync_to_async(_make_account)("ua1")
    await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        topic_id=42,
        forum_chat_id=-100111,
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 42, 999, "hello", send=send)  # 999 not allowed

    assert calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_wrong_forum_is_silent(settings):
    """
    GIVEN the message arrives from a forum_chat_id that is NOT TELEGRAM_FORUM_CHAT_ID
    WHEN  handle_forum_reply is called
    THEN  nothing is sent (silent drop).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    account = await database_sync_to_async(_make_account)("wf1")
    await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        topic_id=42,
        forum_chat_id=-100222,  # different forum stored in thread
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100222, 42, 111, "hello", send=send)  # wrong forum

    assert calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_topic_id_collision_scoped_by_forum(settings):
    """
    GIVEN two threads share the same telegram_topic_id but belong to different forums
    WHEN  handle_forum_reply is called with the second forum's chat_id
    THEN  the lookup returns the thread for the correct forum (proves forum scoping).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100222"

    account = await database_sync_to_async(_make_account)("col1")
    # Thread A — forum 111 (wrong forum for this test)
    await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        topic_id=77,
        forum_chat_id=-100111,
    )
    # Thread B — forum 222 (the configured forum; reply targets this one)
    thread_b = await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.OBSERVED,
        topic_id=77,  # same topic_id as Thread A
        forum_chat_id=-100222,
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100222, 77, 111, "hello", send=send)

    # Must have got a reply (thread_b found), and into the right topic
    assert len(calls) == 1
    assert calls[0]["message_thread_id"] == 77
    # Verify it was thread_b that was resolved (read-only reply expected)
    assert "read-only" in calls[0]["text"].lower()

    # Confirm the DB lookup would NOT return thread_a for the forum-222 query
    from apps.telegram.service import _lookup_thread_for_topic

    resolved = await _lookup_thread_for_topic(-100222, 77)
    assert resolved is not None
    assert resolved.id == thread_b.id


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_pty_session_without_host_is_read_only(settings):
    """
    GIVEN a PTY-mode thread that has no host linked
    WHEN  handle_forum_reply is called
    THEN  a read-only reply is sent (missing host = not driveable).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    account = await database_sync_to_async(_make_account)("ph1")
    await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        topic_id=55,
        forum_chat_id=-100111,
        host=None,  # no host
        tmux="my-session",
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 55, 111, "hello", send=send)

    assert len(calls) == 1
    assert "read-only" in calls[0]["text"].lower()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_pty_session_with_host_and_tmux_creates_approval_prompt(settings):
    """
    GIVEN a PTY-mode thread with a host and a tmux_session_name (driveable session)
    WHEN  handle_forum_reply is called with a reply text
    THEN  an approval prompt message is sent into that topic (Phase 5 gate),
          and an APPROVAL Prompt is created in the DB with the reply text bound.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("pty1")
    host = await database_sync_to_async(_make_host)("host-pty1")
    thread = await database_sync_to_async(_make_thread)(
        account,
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        topic_id=66,
        forum_chat_id=-100111,
        host=host,
        tmux="session-abc",
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 66, 111, "hello", send=send)

    # One approval message delivered to the same topic
    assert len(calls) == 1
    assert calls[0]["message_thread_id"] == 66
    # The message should mention "inject" (it's an approval request)
    assert "inject" in calls[0]["text"].lower()

    # An APPROVAL Prompt must be created in DB with the text bound
    @database_sync_to_async
    def _get_prompt():
        return Prompt.objects.filter(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
        ).first()

    prompt = await _get_prompt()
    assert prompt is not None
    assert prompt.surface_message_ref["action"] == "pty_inject"
    assert prompt.surface_message_ref["inject_text"] == "hello"


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_auto_approve_injects_directly_no_prompt(settings):
    """
    GIVEN a PTY-mode thread with host, tmux_session_name, and metadata auto_approve=True
    WHEN  handle_forum_reply is called with a reply text
    THEN  async_send_pty_input is called with approved=True,
          no APPROVAL Prompt is created in the DB,
          and nothing is sent to the Telegram topic.
    """
    from unittest.mock import AsyncMock, patch

    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("aa1")
    host = await database_sync_to_async(_make_host)("host-aa1")
    thread = await database_sync_to_async(
        lambda: Thread.objects.create(
            name="fr-thread-auto-approve",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            metadata={
                "telegram_topic_id": 88,
                "telegram_forum_chat_id": -100111,
                "tmux_session_name": "session-aa",
                "auto_approve": True,
            },
        )
    )()

    mock_inject = AsyncMock()
    send, send_calls = await _make_send()

    with patch("apps.hostlink.service.async_send_pty_input", mock_inject):
        await handle_forum_reply(-100111, 88, 111, "ls -la", send=send)

    # async_send_pty_input must have been called once with approved=True
    mock_inject.assert_called_once()
    _, call_kwargs = mock_inject.call_args
    assert call_kwargs.get("approved") is True
    # positional arg [1] is the text
    assert mock_inject.call_args.args[1] == "ls -la"

    # No Telegram message should have been sent (no approval prompt delivered)
    assert send_calls == []

    # No APPROVAL Prompt created in DB
    @database_sync_to_async
    def _count_prompts():
        return Prompt.objects.filter(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
        ).count()

    assert await _count_prompts() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_forum_reply_without_auto_approve_still_creates_approval_prompt(settings):
    """
    GIVEN a PTY-mode thread with host and tmux but WITHOUT auto_approve in metadata
    WHEN  handle_forum_reply is called
    THEN  an APPROVAL Prompt is created (normal approval gate applies),
          and async_send_pty_input is NOT called directly.
    """
    from unittest.mock import AsyncMock, patch

    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("noaa1")
    host = await database_sync_to_async(_make_host)("host-noaa1")
    thread = await database_sync_to_async(
        lambda: Thread.objects.create(
            name="fr-thread-no-auto-approve",
            runtime="claude_code",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            metadata={
                "telegram_topic_id": 99,
                "telegram_forum_chat_id": -100111,
                "tmux_session_name": "session-noaa",
                # no auto_approve key
            },
        )
    )()

    mock_inject = AsyncMock()
    send, send_calls = await _make_send()

    with patch("apps.hostlink.service.async_send_pty_input", mock_inject):
        await handle_forum_reply(-100111, 99, 111, "ls -la", send=send)

    # async_send_pty_input must NOT have been called directly
    mock_inject.assert_not_called()

    # An APPROVAL Prompt must have been created
    @database_sync_to_async
    def _count_prompts():
        return Prompt.objects.filter(
            thread=thread,
            prompt_type=Prompt.PromptType.APPROVAL,
        ).count()

    assert await _count_prompts() == 1

    # The approval request was delivered to Telegram
    assert len(send_calls) == 1
    assert "inject" in send_calls[0]["text"].lower()
