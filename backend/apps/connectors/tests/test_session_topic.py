"""A connector session gets its own Telegram forum topic.

start_session creates a per-session topic and stores its id on the thread;
ask/notify deliver INTO that topic; a reply there resolves the pending question
(scoped to that thread). This keeps the whole session in one channel the
operator both reads and replies in — not the shared General channel.
"""

from unittest.mock import patch

import pytest

from apps.connectors.service import (
    ask,
    notify,
    resolve_pending_ask,
    start_session,
)
from apps.prompts.models import Prompt
from apps.threads.models import Thread


@pytest.fixture
def telegram_forum(settings):
    settings.ORC_MESSAGING_PLATFORM = "telegram"
    settings.ORC_PROMPT_CHAT_ID = "-100999"
    settings.TELEGRAM_FORUM_CHAT_ID = "-100999"
    return -100999


@pytest.mark.django_db
def test_start_session_creates_topic_and_stores_metadata(telegram_forum):
    """
    GIVEN telegram is the active platform
    WHEN  start_session runs
    THEN  it creates a forum topic and stores telegram_topic_id +
          telegram_forum_chat_id on the session thread, and announces into it.
    """
    sent = []

    async def fake_create_topic(chat_id, name, color):
        return 4242

    async def fake_send(chat_id, text, message_thread_id=None, **kwargs):
        sent.append((chat_id, message_thread_id, text))

    with (
        patch("apps.telegram.telegram_api.create_forum_topic", fake_create_topic),
        patch("apps.telegram.telegram_api.send_message", fake_send),
    ):
        out = start_session("conn-s", "claude_code", "/tmp/ws", "My session")

    thread = Thread.objects.get(id=out["thread_id"])
    assert thread.metadata["telegram_topic_id"] == 4242
    assert thread.metadata["telegram_forum_chat_id"] == telegram_forum
    # The start announcement went INTO the topic.
    assert sent and sent[0][1] == 4242


@pytest.mark.django_db
def test_ask_delivers_into_session_topic(telegram_forum):
    """
    GIVEN a session whose thread owns a forum topic
    WHEN  ask_human creates a FREE_TEXT prompt for that connector
    THEN  the question is delivered INTO the session topic, not General.
    """
    delivered = []

    async def fake_create_topic(chat_id, name, color):
        return 555

    async def fake_send(chat_id, text, message_thread_id=None, **kwargs):
        delivered.append((chat_id, message_thread_id, text))

    with (
        patch("apps.telegram.telegram_api.create_forum_topic", fake_create_topic),
        patch("apps.telegram.telegram_api.send_message", fake_send),
    ):
        start_session("conn-a", "claude_code", "/tmp/ws", "S")
        delivered.clear()
        ask("conn-a", "claude_code", "/tmp/ws", "What next?", [])

    assert len(delivered) == 1
    chat_id, thread_id, text = delivered[0]
    assert chat_id == telegram_forum
    assert thread_id == 555
    assert "What next?" in text


@pytest.mark.django_db
def test_notify_delivers_into_session_topic(telegram_forum):
    """
    GIVEN a session whose thread owns a forum topic
    WHEN  notify pushes progress
    THEN  it is delivered INTO the session topic.
    """
    delivered = []

    async def fake_create_topic(chat_id, name, color):
        return 777

    async def fake_send(chat_id, text, message_thread_id=None, **kwargs):
        delivered.append((chat_id, message_thread_id, text))

    with (
        patch("apps.telegram.telegram_api.create_forum_topic", fake_create_topic),
        patch("apps.telegram.telegram_api.send_message", fake_send),
    ):
        start_session("conn-n", "claude_code", "/tmp/ws", "S")
        delivered.clear()
        notify("conn-n", "claude_code", "/tmp/ws", "progress update")

    assert delivered == [(telegram_forum, 777, "progress update")]


@pytest.mark.django_db
def test_resolve_pending_ask_scopes_to_thread():
    """
    GIVEN two sessions each with a pending FREE_TEXT question
    WHEN  resolve_pending_ask is called scoped to one thread
    THEN  only that thread's question is answered.
    """
    nonce_a = ask("conn-x", "claude_code", "/tmp/ws", "A?", [])
    ask("conn-y", "claude_code", "/tmp/ws", "B?", [])

    thread_a = Prompt.objects.get(nonce=nonce_a).thread

    resolved = resolve_pending_ask("answer-a", by="111", thread=thread_a)

    assert resolved.nonce == nonce_a
    assert resolved.status == Prompt.StatusChoices.ANSWERED
    # The other thread's question is untouched.
    assert Prompt.objects.exclude(nonce=nonce_a).get().status == Prompt.StatusChoices.PENDING
