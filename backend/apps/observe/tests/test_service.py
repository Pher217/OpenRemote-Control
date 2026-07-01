"""Tests for record_turn idempotency (apps.observe.service).

source_event_key is the DB-level idempotency key for daemon-tailed transcript
turns: a daemon restart or ws reconnect can re-send the same transcript event,
and a TTL cache is not correctness — only a DB constraint is.
"""

import pytest
from asgiref.sync import sync_to_async

from apps.accounts.models import Account
from apps.hosts.models import Host
from apps.observe.service import record_turn
from apps.threads.models import Message, Thread


async def _make_thread():
    host = await sync_to_async(Host.objects.create)(slug="svc-1", name="svc-1", os="linux")
    account = await sync_to_async(Account.objects.create)(
        provider="pty", label="orc-run", auth_type="none", credential_type="none"
    )
    return await sync_to_async(Thread.objects.create)(
        name="svc-test",
        runtime="pty",
        runtime_mode=Thread.RuntimeModeChoices.PTY,
        host=host,
        account=account,
        status=Thread.StatusChoices.RUNNING,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_same_source_event_key_twice_returns_none_second_time():
    """
    GIVEN a thread and a source_event_key already recorded
    WHEN record_turn is called again with the SAME source_event_key
    THEN the second call returns None and no second row is created.
    """
    thread = await _make_thread()

    first = await record_turn(thread, "assistant", "hello", source_event_key="evt-1")
    second = await record_turn(thread, "assistant", "hello again", source_event_key="evt-1")

    assert first is not None
    assert second is None
    count = await sync_to_async(Message.objects.filter(thread=thread).count)()
    assert count == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_different_source_event_keys_both_recorded():
    """
    GIVEN a thread
    WHEN record_turn is called twice with DIFFERENT source_event_keys
    THEN both turns are persisted as separate rows.
    """
    thread = await _make_thread()

    first = await record_turn(thread, "assistant", "one", source_event_key="evt-a")
    second = await record_turn(thread, "assistant", "two", source_event_key="evt-b")

    assert first is not None
    assert second is not None
    count = await sync_to_async(Message.objects.filter(thread=thread).count)()
    assert count == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_no_source_event_key_preserves_legacy_behavior():
    """
    GIVEN a thread
    WHEN record_turn is called twice with NO source_event_key (legacy callers)
    THEN both calls succeed and two distinct rows are created (no dedup).
    """
    thread = await _make_thread()

    first = await record_turn(thread, "assistant", "hi")
    second = await record_turn(thread, "assistant", "hi")

    assert first is not None
    assert second is not None
    assert first.id != second.id
    count = await sync_to_async(Message.objects.filter(thread=thread).count)()
    assert count == 2
