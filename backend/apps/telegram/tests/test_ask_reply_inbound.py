"""Inbound routing: a typed reply answers a pending ask_human question.

When ask_human has delivered a FREE_TEXT prompt to the operator's chat, the
operator's next typed message must resolve that prompt (request -> answer
driving) rather than being dispatched to the chat LLM.
"""

import pytest
from channels.db import database_sync_to_async

from apps.connectors.service import ask, result
from apps.telegram.service import handle_update


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typed_reply_resolves_pending_ask_human(monkeypatch, settings):
    """
    GIVEN a pending ask_human FREE_TEXT prompt and an allowlisted operator
    WHEN  the operator types a message in the prompt chat
    THEN  the prompt is answered with that text, a confirmation is sent, and the
          chat LLM is NOT invoked.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}

    nonce = await database_sync_to_async(ask)(
        "conn-tg", "claude_code", "/tmp/ws", "What next?", []
    )

    dispatched = []

    async def _no_dispatch(*args, **kwargs):
        dispatched.append(args)

    monkeypatch.setattr("apps.telegram.service.dispatch_text", _no_dispatch)

    sent = []

    async def cap(cid, txt, **kwargs):
        sent.append((cid, txt))

    await handle_update(12345, "run the tests", from_user_id=12345, send=cap)

    assert dispatched == []
    assert len(sent) == 1
    assert sent[0][0] == 12345
    assert "✓" in sent[0][1]

    data = await database_sync_to_async(result)(nonce)
    assert data == {"status": "answered", "answer": "run the tests"}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_typed_message_dispatches_to_llm_when_no_pending_ask(monkeypatch, settings):
    """
    GIVEN no pending ask_human prompt
    WHEN  the operator types a message
    THEN  it is dispatched to the chat LLM (normal behaviour preserved).
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}

    dispatched = []

    async def _capture_dispatch(thread, text, **kwargs):
        dispatched.append(text)

    monkeypatch.setattr("apps.telegram.service.dispatch_text", _capture_dispatch)

    async def cap(cid, txt, **kwargs):
        pass

    await handle_update(12345, "hello there", from_user_id=12345, send=cap)

    assert dispatched == ["hello there"]
