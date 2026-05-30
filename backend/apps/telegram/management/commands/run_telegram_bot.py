import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.telegram.service import handle_update
from apps.telegram.telegram_api import get_updates, redact_token, send_message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the Telegram long-poll bot loop."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        if not settings.TELEGRAM_BOT_TOKEN:
            self.stderr.write("TELEGRAM_BOT_TOKEN is not set; aborting.")
            return

        self.stdout.write("Telegram bot started; polling for updates.")
        offset = 0
        while True:
            try:
                updates = await get_updates(offset)
            except Exception as exc:
                logger.error("telegram getUpdates failed: %s", redact_token(repr(exc)))
                await asyncio.sleep(3)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    message = update.get("message")
                    if not message or "text" not in message:
                        continue
                    chat_id = message["chat"]["id"]
                    text = message["text"]
                    await handle_update(chat_id, text, send=send_message)
                except Exception as exc:
                    logger.error(
                        "telegram update handling failed: %s", redact_token(repr(exc))
                    )
                    continue
