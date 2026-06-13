import json as _json

import pytest
from channels.db import database_sync_to_async

from apps.telegram.models import TelegramChat
from apps.telegram.service import get_or_create_thread_for_chat, handle_update
from apps.telegram.telegram_api import redact_token
from apps.threads.models import Message, Thread


def test_redact_token_strips_secret_from_error_text(settings):
    """GIVEN a token in error text WHEN redacted THEN the token never survives."""
    settings.TELEGRAM_BOT_TOKEN = "123456:SECRETtoken"
    msg = "Client error for url 'https://api.telegram.org/bot123456:SECRETtoken/getUpdates'"
    out = redact_token(msg)
    assert "SECRETtoken" not in out
    assert "123456:SECRETtoken" not in out
    assert "***" in out


class _FakeOllamaResponse:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeOllamaClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, json=None):
        lines = [
            _json.dumps(
                {"message": {"role": "assistant", "content": "Hello"}, "done": False}
            ),
            _json.dumps(
                {"message": {"role": "assistant", "content": " there"}, "done": False}
            ),
            _json.dumps(
                {"message": {"role": "assistant", "content": ""}, "done": True}
            ),
        ]
        return _FakeOllamaResponse(lines)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_handle_update_dispatches_and_replies(monkeypatch, settings):
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _FakeOllamaClient)

    sent = []

    async def cap(cid, txt):
        sent.append((cid, txt))

    await handle_update(12345, "Say hi", send=cap)

    @database_sync_to_async
    def _state():
        chat = TelegramChat.objects.select_related("thread").get(chat_id=12345)
        contents = list(
            Message.objects.filter(
                thread=chat.thread, role="assistant"
            ).values_list("redacted_content", flat=True)
        )
        return chat.thread.id, contents

    thread_id, contents = await _state()
    assert thread_id is not None
    assert contents == ["Hello there"]
    assert sent == [(12345, "Hello there")]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_non_allowlisted_ignored(monkeypatch, settings):
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}
    monkeypatch.setattr("apps.tier2.ollama.httpx.AsyncClient", _FakeOllamaClient)

    sent = []

    async def cap(cid, txt):
        sent.append((cid, txt))

    await handle_update(999, "hi", send=cap)

    @database_sync_to_async
    def _exists():
        return TelegramChat.objects.filter(chat_id=999).exists()

    assert sent == []
    assert await _exists() is False


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_slash_stop_no_args_shows_usage(monkeypatch, settings):
    """
    GIVEN an allowlisted operator sends /stop with no session argument
    WHEN handle_update processes the message
    THEN a Usage reply is sent and no threads are modified.
    """
    settings.TELEGRAM_ALLOWED_CHAT_IDS = {12345}

    sent = []

    async def cap(cid, txt, **kwargs):
        sent.append((cid, txt))

    await handle_update(12345, "/stop", send=cap)

    assert len(sent) == 1
    assert sent[0][0] == 12345
    assert "Usage" in sent[0][1]
