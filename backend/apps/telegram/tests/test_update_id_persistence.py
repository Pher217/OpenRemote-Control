"""Tests for update_id offset persistence in run_telegram_bot.

The bot command's _run() loop reads cache at startup to seed the initial offset,
then writes the last-seen update_id after each handled update.

We test by monkeypatching get_updates and handle_update at the command module
level, then running _run() for a controlled number of iterations.  A StopIteration
sentinel propagated out of get_updates breaks the loop early.

Uses Django's LocMemCache so no Redis is needed.
"""

from __future__ import annotations

import asyncio

import pytest
from django.test import override_settings

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

_CACHE_KEY = "telegram:last_update_id"


def _make_text_update(update_id: int, text: str = "hello", chat_id: int = 11111) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "text": text,
            "from": {"id": chat_id},
        },
    }


class _StopLoop(BaseException):
    """Sentinel: raised by the fake get_updates after N calls to break the bot loop.

    Inherits from BaseException (not Exception) so it propagates through the
    bot's ``except Exception`` handler without being swallowed.
    """


@override_settings(
    TELEGRAM_BOT_TOKEN="fake-token",
    TELEGRAM_ALLOWED_CHAT_IDS={11111},
    CACHES=LOCMEM_CACHES,
)
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bot_seeds_offset_from_cache(monkeypatch):
    """
    GIVEN last_update_id=200 persisted in cache from a previous run
    WHEN the bot starts
    THEN the first getUpdates call uses offset=201.
    """
    from django.core.cache import cache

    cache.set(_CACHE_KEY, 200)

    import apps.telegram.management.commands.run_telegram_bot as cmd_module

    captured_offsets: list[int] = []
    call_count = 0

    async def fake_get_updates(offset):
        nonlocal call_count
        call_count += 1
        captured_offsets.append(offset)
        if call_count == 1:
            return [_make_text_update(201)]
        raise _StopLoop("done")

    async def fake_handle_update(chat_id, text, *, from_user_id, send):
        pass

    monkeypatch.setattr(cmd_module, "get_updates", fake_get_updates)
    monkeypatch.setattr(cmd_module, "handle_update", fake_handle_update)

    from apps.telegram.management.commands.run_telegram_bot import Command

    cmd = Command()

    try:
        await cmd._run()
    except _StopLoop:
        pass

    assert captured_offsets[0] == 201, (
        f"Expected first offset=201 (seeded from cache last_update_id=200), got {captured_offsets[0]}"
    )


@override_settings(
    TELEGRAM_BOT_TOKEN="fake-token",
    TELEGRAM_ALLOWED_CHAT_IDS={11111},
    CACHES=LOCMEM_CACHES,
)
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bot_advances_cache_after_handling(monkeypatch):
    """
    GIVEN the cache is empty (first run)
    WHEN the bot processes update_id=100 successfully
    THEN cache contains 100 after the iteration.
    """
    from django.core.cache import cache

    cache.delete(_CACHE_KEY)

    import apps.telegram.management.commands.run_telegram_bot as cmd_module

    call_count = 0

    async def fake_get_updates(offset):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [_make_text_update(100)]
        raise _StopLoop("done")

    async def fake_handle_update(chat_id, text, *, from_user_id, send):
        pass

    monkeypatch.setattr(cmd_module, "get_updates", fake_get_updates)
    monkeypatch.setattr(cmd_module, "handle_update", fake_handle_update)

    from apps.telegram.management.commands.run_telegram_bot import Command

    cmd = Command()

    try:
        await cmd._run()
    except _StopLoop:
        pass

    stored = cache.get(_CACHE_KEY)
    assert stored == 100, f"Expected cache to hold update_id=100, got {stored!r}"


@override_settings(
    TELEGRAM_BOT_TOKEN="fake-token",
    TELEGRAM_ALLOWED_CHAT_IDS={11111},
    CACHES=LOCMEM_CACHES,
)
@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_bot_skips_update_id_lte_last_stored(monkeypatch):
    """
    GIVEN last_update_id=50 in cache
    WHEN getUpdates returns update_id=50 (a duplicate / race)
    THEN handle_update is NOT called for that id.
    """
    from django.core.cache import cache

    cache.set(_CACHE_KEY, 50)

    import apps.telegram.management.commands.run_telegram_bot as cmd_module

    call_count = 0
    handled: list[int] = []

    async def fake_get_updates(offset):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Return the already-processed id (simulating a race / stale offset).
            return [_make_text_update(50)]
        raise _StopLoop("done")

    async def fake_handle_update(chat_id, text, *, from_user_id, send):
        handled.append(chat_id)

    monkeypatch.setattr(cmd_module, "get_updates", fake_get_updates)
    monkeypatch.setattr(cmd_module, "handle_update", fake_handle_update)

    from apps.telegram.management.commands.run_telegram_bot import Command

    cmd = Command()

    try:
        await cmd._run()
    except _StopLoop:
        pass

    assert handled == [], (
        f"handle_update should not be called for update_id <= last_stored (50), but was called: {handled}"
    )
