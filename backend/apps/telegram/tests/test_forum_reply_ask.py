"""A reply in a connector session's topic answers its pending ask_human question.

The connector session owns a forum topic; a typed reply there resolves the
pending FREE_TEXT prompt for that thread (request -> answer driving), instead of
the read-only bounce that a non-driveable thread would otherwise get.
"""

import pytest
from channels.db import database_sync_to_async

from apps.accounts.models import Account
from apps.prompts.models import Prompt
from apps.prompts.service import create_prompt
from apps.telegram.service import handle_forum_reply
from apps.threads.models import Thread


def _make_connector_thread(topic_id, forum_chat_id):
    account = Account.objects.create(
        provider="connector",
        label="connector",
        auth_type="none",
        credential_type="none",
    )
    return Thread.objects.create(
        name="this-coding-session",
        runtime="claude_code",
        runtime_mode=Thread.RuntimeModeChoices.API,
        account=account,
        metadata={
            "telegram_topic_id": topic_id,
            "telegram_forum_chat_id": forum_chat_id,
        },
    )


async def _make_send():
    calls = []

    async def send(chat_id, text, message_thread_id=None, reply_markup=None):
        calls.append({"chat_id": chat_id, "text": text, "thread": message_thread_id})

    return send, calls


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reply_in_session_topic_resolves_pending_ask(settings):
    """
    GIVEN a connector session topic with a pending FREE_TEXT question
    WHEN  the operator replies in that topic
    THEN  the question is answered with the typed text and a confirmation sent.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    thread = await database_sync_to_async(_make_connector_thread)(42, -100111)
    prompt = await database_sync_to_async(create_prompt)(
        thread,
        prompt_type=Prompt.PromptType.FREE_TEXT,
        question="What next?",
        trust_class=Prompt.TrustClass.DECISION,
    )

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 42, 111, "run the tests", send=send)

    assert len(calls) == 1
    assert calls[0]["thread"] == 42
    assert "✓" in calls[0]["text"]

    @database_sync_to_async
    def _reload():
        return Prompt.objects.get(id=prompt.id)

    answered = await _reload()
    assert answered.status == Prompt.StatusChoices.ANSWERED
    assert answered.response == {"text": "run the tests"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_reply_in_session_topic_without_pending_ask_bounces_read_only(settings):
    """
    GIVEN a connector session topic with NO pending question
    WHEN  the operator replies in that topic
    THEN  the existing read-only bounce applies (no crash, no resolution).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {111}
    settings.TELEGRAM_FORUM_CHAT_ID = "-100111"

    await database_sync_to_async(_make_connector_thread)(43, -100111)

    send, calls = await _make_send()
    await handle_forum_reply(-100111, 43, 111, "hello", send=send)

    assert len(calls) == 1
    assert "read-only" in calls[0]["text"].lower()
