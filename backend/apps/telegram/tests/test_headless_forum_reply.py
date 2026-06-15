"""Tests for headless Claude relay — Telegram forum-reply routing.

Covers:
- Headless thread reply → send_host_command called with headless.prompt and correct fields.
- No approval Prompt created for headless sessions.
- Headless threads appear in _list_drivable_topics.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.hosts.models import Host
from apps.telegram.service import _list_drivable_topics, handle_forum_reply
from apps.threads.models import Thread


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_account(suffix):
    return Account.objects.create(
        provider="anthropic",
        label=f"test-hl-{suffix}",
        auth_type="oauth",
        credential_type="token",
        encrypted_credential=b"z",
        credential_key_id=f"k-hl-{suffix}",
        credential_recipient=f"r-hl-{suffix}",
    )


def _make_host(slug):
    return Host.objects.create(slug=slug, name=slug, os="linux")


async def _make_send():
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
# handle_forum_reply — headless dispatch
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_reply_calls_send_host_command_not_prompt(settings):
    """
    GIVEN a headless Thread with a host and claude_session_id, linked to a topic
    WHEN an allowed user sends a message in that topic
    THEN send_host_command is called once with name 'headless.prompt' and the
         correct fields (claude_session_id, text, thread_id, started, cwd),
         and NO approval Prompt is created in the DB.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    from apps.prompts.models import Prompt

    account = await database_sync_to_async(_make_account)("hl1")
    host = await database_sync_to_async(_make_host)("host-hl1")
    session_id = str(uuid.uuid4())
    thread = await database_sync_to_async(
        lambda: Thread.objects.create(
            name="headless-relay-thread",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            status=Thread.StatusChoices.RUNNING,
            metadata={
                "headless": True,
                "claude_session_id": session_id,
                "cwd": "/home/user/project",
                "tmux_session_name": None,
                "telegram_topic_id": 200,
                "telegram_forum_chat_id": -100111,
            },
        )
    )()

    captured = []

    def fake_send_host_command(h, command, **kwargs):
        captured.append({"host": h, "command": command, **kwargs})

    send, send_calls = await _make_send()

    with patch("apps.hostlink.service.send_host_command", fake_send_host_command):
        await handle_forum_reply(-100111, 200, 111, "What is 2+2?", send=send)

    # send_host_command called exactly once with headless.prompt
    assert len(captured) == 1
    cmd = captured[0]
    assert cmd["command"] == "headless.prompt"
    assert cmd["claude_session_id"] == session_id
    assert cmd["text"] == "What is 2+2?"
    assert cmd["thread_id"] == str(thread.id)
    assert cmd["started"] is False  # claude_session_started not yet set
    assert cmd["cwd"] == "/home/user/project"

    # No Telegram message sent (headless routes silently)
    assert send_calls == []

    # No APPROVAL Prompt created
    @database_sync_to_async
    def _count_prompts():
        return Prompt.objects.filter(thread=thread).count()

    assert await _count_prompts() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_reply_started_flag_reflects_metadata(settings):
    """
    GIVEN a headless Thread where claude_session_started=True in metadata
    WHEN an allowed user sends a message
    THEN the started=True is passed to send_host_command.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    account = await database_sync_to_async(_make_account)("hl2")
    host = await database_sync_to_async(_make_host)("host-hl2")
    session_id = str(uuid.uuid4())
    await database_sync_to_async(
        lambda: Thread.objects.create(
            name="headless-relay-started",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            status=Thread.StatusChoices.RUNNING,
            metadata={
                "headless": True,
                "claude_session_id": session_id,
                "cwd": "/tmp",
                "tmux_session_name": None,
                "claude_session_started": True,
                "telegram_topic_id": 201,
                "telegram_forum_chat_id": -100111,
            },
        )
    )()

    captured = []

    def fake_send_host_command(h, command, **kwargs):
        captured.append(kwargs)

    send, _ = await _make_send()

    with patch("apps.hostlink.service.send_host_command", fake_send_host_command):
        await handle_forum_reply(-100111, 201, 111, "continue", send=send)

    assert len(captured) == 1
    assert captured[0]["started"] is True


# ---------------------------------------------------------------------------
# _list_drivable_topics — headless threads appear
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_headless_thread_appears_in_drivable_topics(settings):
    """
    GIVEN a headless RUNNING Thread with a telegram_topic_id in the forum
    WHEN _list_drivable_topics is called
    THEN that thread appears in the returned list.
    """
    account = await database_sync_to_async(_make_account)("hl3")
    host = await database_sync_to_async(_make_host)("host-hl3")
    await database_sync_to_async(
        lambda: Thread.objects.create(
            name="headless-topic-visible",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            status=Thread.StatusChoices.RUNNING,
            metadata={
                "headless": True,
                "claude_session_id": str(uuid.uuid4()),
                "tmux_session_name": None,
                "telegram_topic_id": 300,
                "telegram_forum_chat_id": -100555,
            },
        )
    )()

    result = await _list_drivable_topics(-100555)

    assert any(tid == 300 for _name, tid in result)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_pty_and_headless_both_appear_in_drivable_topics():
    """
    GIVEN one headless thread and one PTY+tmux thread both running in the same forum
    WHEN _list_drivable_topics is called
    THEN both appear in the result.
    """
    account = await database_sync_to_async(_make_account)("hl4")
    host = await database_sync_to_async(_make_host)("host-hl4")

    await database_sync_to_async(
        lambda: Thread.objects.create(
            name="headless-both",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            status=Thread.StatusChoices.RUNNING,
            metadata={
                "headless": True,
                "claude_session_id": str(uuid.uuid4()),
                "tmux_session_name": None,
                "telegram_topic_id": 401,
                "telegram_forum_chat_id": -100666,
            },
        )
    )()

    await database_sync_to_async(
        lambda: Thread.objects.create(
            name="pty-both",
            runtime="pty",
            runtime_mode=Thread.RuntimeModeChoices.PTY,
            account=account,
            host=host,
            status=Thread.StatusChoices.RUNNING,
            metadata={
                "tmux_session_name": "orc-xyz",
                "telegram_topic_id": 402,
                "telegram_forum_chat_id": -100666,
            },
        )
    )()

    result = await _list_drivable_topics(-100666)
    topic_ids = [tid for _n, tid in result]
    assert 401 in topic_ids
    assert 402 in topic_ids
