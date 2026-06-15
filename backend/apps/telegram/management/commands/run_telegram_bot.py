"""Run the Telegram long-polling bot and dispatch incoming updates.

Long-running asyncio loop that pulls updates via getUpdates, routes messages
and callback queries to the appropriate handlers, and persists the last
processed update id to avoid replay on restart.
"""
import asyncio
import logging

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from apps.telegram.service import handle_callback_query, handle_forum_reply, handle_update
from apps.telegram.telegram_api import (
    answer_callback_query,
    get_updates,
    redact_token,
    send_message,
)

logger = logging.getLogger(__name__)

# Cache key for persisting the last processed Telegram update_id.
# Written after each successful update handling; read at startup to seed the
# offset so a restart never re-processes updates already handled.
_LAST_UPDATE_ID_KEY = "telegram:last_update_id"


class Command(BaseCommand):
    help = "Run the Telegram long-poll bot loop."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write("TELEGRAM_BOT_TOKEN is not set; aborting.")
            return

        self.stdout.write("Telegram bot started; polling for updates.")

        # Seed offset from the last persisted update_id so a crash/restart does
        # not replay updates that were already processed.  cache.get returns None
        # when the key is absent (first run or cache cleared), in which case we
        # start from 0 (Telegram delivers all pending updates).
        last_stored = cache.get(_LAST_UPDATE_ID_KEY)
        offset = (last_stored + 1) if last_stored is not None else 0

        while True:
            try:
                updates = await get_updates(offset)
            except Exception as exc:
                logger.error("telegram getUpdates failed: %s", redact_token(repr(exc)))
                await asyncio.sleep(3)
                continue

            for update in updates:
                update_id = update["update_id"]

                # Guard: never re-process an id we have already handled.
                # Under normal operation getUpdates with the correct offset
                # prevents this, but the guard is cheap and crash-safe.
                if last_stored is not None and update_id <= last_stored:
                    continue

                # Advance the offset immediately so getUpdates acks this id even
                # if handling raises below.
                offset = update_id + 1

                try:
                    if "callback_query" in update:
                        cq = update["callback_query"]
                        await handle_callback_query(
                            cq["id"],
                            cq["from"]["id"],
                            cq.get("data", ""),
                            answer=answer_callback_query,
                        )
                    else:
                        message = update.get("message")
                        if not message or "text" not in message:
                            # Mark handled even for ignored message types.
                            cache.set(_LAST_UPDATE_ID_KEY, update_id)
                            last_stored = update_id
                            continue
                        chat_id = message["chat"]["id"]
                        text = message["text"]
                        message_thread_id = message.get("message_thread_id")
                        from_user_id = message.get("from", {}).get("id")
                        if message_thread_id is not None:
                            await handle_forum_reply(
                                chat_id,
                                message_thread_id,
                                from_user_id,
                                text,
                                send=send_message,
                            )
                        else:
                            await handle_update(chat_id, text, from_user_id=from_user_id, send=send_message)
                except Exception as exc:
                    logger.error(
                        "telegram update handling failed: %s", redact_token(repr(exc))
                    )
                    # Still persist update_id so a restart doesn't replay the
                    # failed update indefinitely.

                # Persist after successful handling (or after a handled exception
                # — we prefer at-most-once delivery over infinite replay).
                cache.set(_LAST_UPDATE_ID_KEY, update_id)
                last_stored = update_id
