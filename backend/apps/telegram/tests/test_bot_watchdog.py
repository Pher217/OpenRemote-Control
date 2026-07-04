"""Tests for the bot's getUpdates liveness watchdog.

Guards against the silent-stall failure mode (gotcha #26): the bot process
stays alive but an await inside update handling never returns, so no further
getUpdates cycle completes and inbound messages vanish without a log line.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.telegram.management.commands import run_telegram_bot as bot_mod
from apps.telegram.management.commands.run_telegram_bot import (
    WATCHDOG_STALL_SECONDS,
    Command,
    watchdog_is_stalled,
)


def test_fresh_cycle_is_not_stalled():
    """
    GIVEN a poll cycle completed just now
    WHEN the watchdog evaluates staleness
    THEN it reports healthy.
    """
    assert watchdog_is_stalled(1000.0, 1000.0 + WATCHDOG_STALL_SECONDS - 1) is False


def test_stale_cycle_is_stalled():
    """
    GIVEN no poll cycle completed for longer than the stall budget
    WHEN the watchdog evaluates staleness
    THEN it reports stalled.
    """
    assert watchdog_is_stalled(1000.0, 1000.0 + WATCHDOG_STALL_SECONDS + 1) is True


@pytest.mark.asyncio
async def test_watchdog_exits_process_on_stall(monkeypatch):
    """
    GIVEN a last_cycle timestamp far in the past
    WHEN the watchdog task wakes
    THEN it calls os._exit so the supervisor restarts the bot.
    """
    exited = []

    def fake_exit(code):
        exited.append(code)
        raise SystemExit(code)  # stop the watchdog loop in the test

    monkeypatch.setattr(bot_mod.os, "_exit", fake_exit)
    monkeypatch.setattr(bot_mod, "WATCHDOG_INTERVAL", 0.01)

    stale = [0.0]  # monotonic() is far beyond 0 + stall budget
    with pytest.raises(SystemExit):
        await asyncio.wait_for(Command()._watchdog(stale), timeout=5)
    assert exited == [70]


@pytest.mark.asyncio
async def test_watchdog_stays_quiet_while_healthy(monkeypatch):
    """
    GIVEN a last_cycle timestamp that keeps being refreshed
    WHEN the watchdog runs for several intervals
    THEN it never exits.
    """
    import time as _time

    exited = []
    monkeypatch.setattr(bot_mod.os, "_exit", lambda code: exited.append(code))
    monkeypatch.setattr(bot_mod, "WATCHDOG_INTERVAL", 0.01)

    fresh = [_time.monotonic()]
    task = asyncio.create_task(Command()._watchdog(fresh))
    for _ in range(5):
        await asyncio.sleep(0.02)
        fresh[0] = _time.monotonic()
    task.cancel()
    assert exited == []
