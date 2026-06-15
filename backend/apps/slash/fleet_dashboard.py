"""Fleet dashboard — edit-in-place pinned message in the Telegram forum.

Maintains a SINGLE pinned message in the forum's General topic (message_thread_id=None,
i.e. the root "General" topic of the supergroup).  The message is silently posted once,
pinned, then silently edited on subsequent calls.  If the edit fails (message deleted),
a fresh message is sent and re-pinned.

The dashboard message id is stored in Django's cache under DASHBOARD_CACHE_KEY.
This avoids a schema migration and is acceptable for an operator-facing dashboard
(the worst case of a cache miss is a redundant re-post + re-pin).

Pattern: mirrors the digest edit/fallback approach from observe/delivery.py —
  1. Read stored id from cache.
  2. If None  → send + pin + store id.
  3. If Some  → edit; on failure → send + pin + store new id.

call `refresh_fleet_dashboard()` to trigger a refresh.  It is intentionally
synchronous-safe: callers (slash handlers, management commands, Celery tasks) can
await it when they are async, or wrap it with database_sync_to_async when needed.

The pinChatMessage Bot API method is called as a thin wrapper in telegram_api.py;
this module adds that wrapper if it is absent (it is thin enough to inline here
to avoid touching telegram_api.py when the wrapper might already exist).
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache import cache

from apps.slash.handlers.sessions import _active_threads, render_fleet

DASHBOARD_CACHE_KEY = "telegram:fleet_dashboard_message_id"
# TTL of 30 days — effectively permanent; we update it on every refresh.
_CACHE_TTL = 60 * 60 * 24 * 30


async def _pin_message(api, chat_id: int, message_id: int) -> None:
    """Pin a message silently.  Swallows failures (e.g. bot not admin)."""
    with contextlib.suppress(Exception):
        await api.pin_chat_message(chat_id, message_id)


async def refresh_fleet_dashboard(
    *,
    forum_chat_id: int | None = None,
    api=None,
) -> None:
    """Post or edit the pinned fleet dashboard in the Telegram forum.

    Parameters
    ----------
    forum_chat_id:
        The Telegram supergroup/forum chat id.  Falls back to
        ``settings.TELEGRAM_FORUM_CHAT_ID`` when not supplied.
    api:
        The Telegram API module (or a test stub).  Falls back to
        ``apps.telegram.telegram_api`` when not supplied.
    """
    if api is None:
        from apps.telegram import telegram_api as _api

        api = _api

    if forum_chat_id is None:
        raw = getattr(settings, "TELEGRAM_FORUM_CHAT_ID", "") or ""
        if not raw:
            return
        try:
            forum_chat_id = int(raw)
        except (ValueError, TypeError):
            return

    # Build the dashboard text (sync ORM call wrapped in sync_to_async is NOT
    # needed here because this function is only ever called from async context
    # after the thread queryset has already been evaluated, OR via a
    # database_sync_to_async wrapper at the call-site).
    from channels.db import database_sync_to_async

    threads = await database_sync_to_async(_active_threads)()
    now = datetime.now(tz=UTC)
    text = render_fleet(threads, now)

    # Truncate to Telegram's 4096-char limit.
    if len(text) > 4096:
        text = text[:4093] + "…"

    stored_id = cache.get(DASHBOARD_CACHE_KEY)

    if stored_id is None:
        # First time — post + pin.
        new_id = await api.send_message(
            forum_chat_id,
            text,
            message_thread_id=None,  # General topic
            parse_mode="HTML",
            disable_notification=True,
        )
        if new_id is not None:
            cache.set(DASHBOARD_CACHE_KEY, new_id, _CACHE_TTL)
            await _pin_message(api, forum_chat_id, new_id)
        return

    # Edit the existing pinned message.
    edited = await api.edit_message_text(
        forum_chat_id,
        stored_id,
        text,
        parse_mode="HTML",
    )
    if not edited:
        # Edit failed (message deleted or too old) — fall back to a fresh send + re-pin.
        new_id = await api.send_message(
            forum_chat_id,
            text,
            message_thread_id=None,
            parse_mode="HTML",
            disable_notification=True,
        )
        if new_id is not None:
            cache.set(DASHBOARD_CACHE_KEY, new_id, _CACHE_TTL)
            await _pin_message(api, forum_chat_id, new_id)
