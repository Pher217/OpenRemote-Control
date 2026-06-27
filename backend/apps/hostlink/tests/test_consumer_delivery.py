import asyncio

import pytest
from asgiref.sync import sync_to_async
from django.test import override_settings

from apps.hostlink import consumers
from apps.hostlink.consumers import HostDaemonConsumer
from apps.hosts.models import Host


def _make_consumer(host):
    c = HostDaemonConsumer()
    c.host = host
    # Delivery is offloaded to a background drainer (started in connect()); tests
    # set the queue directly.
    c._delivery_queue = asyncio.Queue(maxsize=2000)
    return c


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100999")
async def test_deliver_to_telegram_is_non_blocking(monkeypatch):
    """
    GIVEN a turn to deliver
    WHEN _deliver_to_telegram is called
    THEN it enqueues WITHOUT awaiting deliver_turn (so a slow/429 send can never
         block the receive loop / starve the host heartbeat).
    """
    inline_calls = []

    async def fake_deliver(*a, **k):
        inline_calls.append(1)

    monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)
    host = await sync_to_async(Host.objects.create)(slug="nb1", os="linux")
    consumer = _make_consumer(host)

    await consumer._deliver_to_telegram(object(), {"role": "assistant", "text": "x"})

    assert inline_calls == []  # NOT delivered inline
    assert consumer._delivery_queue.qsize() == 1  # enqueued for the drainer


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100999")
async def test_deliver_queue_drops_oldest_when_full():
    """
    GIVEN the bounded delivery queue is full
    WHEN another turn is enqueued
    THEN the oldest pending turn is dropped to make room for the newest (the turn
         is already persisted; the daemon re-sends on reconnect).
    """
    host = await sync_to_async(Host.objects.create)(slug="nb2", os="linux")
    consumer = _make_consumer(host)
    consumer._delivery_queue = asyncio.Queue(maxsize=2)

    await consumer._deliver_to_telegram(object(), {"text": "oldest"})
    await consumer._deliver_to_telegram(object(), {"text": "middle"})
    await consumer._deliver_to_telegram(object(), {"text": "newest"})  # evicts "oldest"

    assert consumer._delivery_queue.qsize() == 2
    _, first = consumer._delivery_queue.get_nowait()
    _, second = consumer._delivery_queue.get_nowait()
    assert [first["text"], second["text"]] == ["middle", "newest"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_FORUM_CHAT_ID="-100999")
async def test_disconnect_drains_pending_deliveries(monkeypatch):
    """
    GIVEN a turn enqueued with the drainer actually running (production path)
    WHEN the consumer disconnects
    THEN the pending turn is still delivered via cooperative shutdown — not lost
         to task cancellation. (Codex HIGH: in-flight item must not be dropped.)
    """
    calls = []

    async def fake_deliver(thread, parsed, msg, *, forum_chat_id):
        calls.append((parsed["text"], forum_chat_id))

    monkeypatch.setattr(consumers, "deliver_turn", fake_deliver)
    host = await sync_to_async(Host.objects.create)(slug="flush1", os="linux")
    consumer = _make_consumer(host)
    consumer._closing = False
    # Start the real drainer, exactly as connect() does.
    consumer._delivery_task = asyncio.create_task(consumer._delivery_drainer())

    await consumer._deliver_to_telegram(object(), {"role": "assistant", "text": "intro"})

    await consumer.disconnect(1000)

    assert calls == [("intro", -100999)]
    assert consumer._delivery_queue.empty()
