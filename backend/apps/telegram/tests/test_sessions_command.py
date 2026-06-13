"""Tests for the /sessions command wired at the Telegram bot service layer.

These tests verify:
1. An allowlisted operator sending /sessions receives the fleet text AND the
   dashboard is refreshed (both must happen).
2. A NON-allowlisted sender sending /sessions receives nothing and the
   dashboard is NOT refreshed.  (Security regression test — the whole point of
   this fix.)
3. The /sessions command is NOT reachable via the unauthenticated dispatch_text
   slash-handler path (the HANDLERS backdoor is closed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from django.test import override_settings

from apps.telegram.service import handle_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CaptureSend:
    """Async send callable that records calls and accepts parse_mode kwarg."""

    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, chat_id, text, *, parse_mode=None, message_thread_id=None, **kwargs):
        self.calls.append(
            {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        )


# ---------------------------------------------------------------------------
# Test 1 — allowlisted sender gets fleet text + dashboard refresh
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_ALLOWED_CHAT_IDS={12345}, TELEGRAM_FORUM_CHAT_ID="")
async def test_sessions_command_allowlisted_sends_fleet_and_refreshes_dashboard():
    """
    GIVEN an allowlisted operator sends /sessions
    WHEN handle_update processes the message
    THEN render_fleet output is sent exactly once (parse_mode HTML) AND
         refresh_fleet_dashboard is called exactly once.
    """
    send = _CaptureSend()

    with patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=AsyncMock,
    ) as mock_refresh:
        with patch(
            "apps.slash.handlers.sessions._active_threads",
            return_value=[],
        ):
            await handle_update(12345, "/sessions", send=send)

    # Fleet text was sent once.
    assert len(send.calls) == 1
    assert send.calls[0]["chat_id"] == 12345
    assert send.calls[0]["parse_mode"] == "HTML"
    # Content is the "no active sessions" placeholder — empty fleet is fine.
    assert isinstance(send.calls[0]["text"], str)
    assert len(send.calls[0]["text"]) > 0

    # Dashboard was refreshed exactly once.
    mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Test 2 — NON-allowlisted sender gets nothing (security regression test)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@override_settings(TELEGRAM_ALLOWED_CHAT_IDS={12345}, TELEGRAM_FORUM_CHAT_ID="")
async def test_sessions_command_non_allowlisted_is_silently_dropped():
    """
    GIVEN a sender whose chat_id is NOT in TELEGRAM_ALLOWED_CHAT_IDS sends /sessions
    WHEN handle_update processes the message
    THEN nothing is sent to the non-allowlisted sender AND the dashboard is NOT refreshed.

    This is the security regression test: /sessions must never expose the full
    fleet to an unauthenticated caller.
    """
    send = _CaptureSend()

    with patch(
        "apps.telegram.service.refresh_fleet_dashboard",
        new_callable=AsyncMock,
    ) as mock_refresh:
        await handle_update(999, "/sessions", send=send)

    # Nothing sent.
    assert send.calls == []
    # Dashboard not refreshed.
    mock_refresh.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — /sessions is NOT in HANDLERS (unauthenticated dispatch_text path closed)
# ---------------------------------------------------------------------------


def test_sessions_not_in_slash_handlers():
    """
    GIVEN the slash HANDLERS registry
    WHEN get_handler("sessions") is called
    THEN None is returned, proving the unauthenticated dispatch_text backdoor is closed.
    """
    from apps.slash.handlers import get_handler

    assert get_handler("sessions") is None
