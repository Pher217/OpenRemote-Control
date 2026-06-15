import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from apps.hostlink import consumers
from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host


def _make_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    c._file_sessions = {}
    return c


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100999")
async def test_session_event_delivers_to_telegram(monkeypatch):
    """
    GIVEN a configured forum chat
    WHEN the consumer handles a session.event with a turn
    THEN deliver_turn is called once with the parsed turn and the forum chat id.
    """
    calls = []

    async def fake_deliver(thread, parsed, msg, *, forum_chat_id):
        calls.append((parsed, forum_chat_id))

    monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)

    host = await sync_to_async(Host.objects.create)(slug="h1", os="linux")
    consumer = _make_consumer(host)

    await consumer._handle_session_event(
        {
            "session_id": "S-evt-1",
            "jsonl_path": "/tmp/a.jsonl",
            "provider": "claude_code",
            "role": "user",
            "text": "hello from windows",
        }
    )

    assert len(calls) == 1
    parsed, forum_chat_id = calls[0]
    assert parsed["role"] == "user"
    assert parsed["text"] == "hello from windows"
    assert forum_chat_id == -100999


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="")
async def test_no_delivery_when_forum_unset(monkeypatch):
    """
    GIVEN no forum chat configured
    WHEN the consumer handles a session.event with a turn
    THEN deliver_turn is never called (ingestion still proceeds).
    """
    calls = []

    async def fake_deliver(*a, **k):
        calls.append(1)

    monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)

    host = await sync_to_async(Host.objects.create)(slug="h2", os="linux")
    consumer = _make_consumer(host)

    await consumer._handle_session_event(
        {
            "session_id": "S-evt-2",
            "jsonl_path": "/tmp/b.jsonl",
            "provider": "claude_code",
            "role": "assistant",
            "text": "no forum -> no delivery",
        }
    )

    assert calls == []
