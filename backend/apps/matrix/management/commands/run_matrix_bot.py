import asyncio
import logging

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the Matrix nio sync bot loop."

    def handle(self, *args, **options):
        asyncio.run(self._run())

    async def _run(self):
        # Lazy import: nio must not be required at module/app import time.
        # If matrix-nio is not installed the management command will error here
        # when invoked, but the rest of the app will load fine.
        try:
            import nio  # noqa: F401
            from nio import AsyncClient, RoomMessageText
        except ImportError:
            self.stderr.write(
                "matrix-nio is not installed. "
                "Run: pip install matrix-nio  (or add it to backend dependencies)."
            )
            return

        from apps.matrix.service import handle_message

        homeserver = getattr(settings, "MATRIX_HOMESERVER", "")
        user_id = getattr(settings, "MATRIX_USER_ID", "")
        access_token = getattr(settings, "MATRIX_ACCESS_TOKEN", "")

        if not all([homeserver, user_id, access_token]):
            self.stderr.write(
                "MATRIX_HOMESERVER, MATRIX_USER_ID, and MATRIX_ACCESS_TOKEN "
                "must all be set; aborting."
            )
            return

        allowed_rooms: list[str] = getattr(settings, "MATRIX_ALLOWED_ROOMS", [])

        client = AsyncClient(homeserver, user_id)
        client.access_token = access_token

        self.stdout.write(f"Matrix bot started as {user_id} on {homeserver}.")

        async def _send(room_id: str, text: str) -> None:
            try:
                await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": text},
                )
            except Exception:
                logger.exception("matrix send failed room=%s", room_id)

        async def _on_message(room, event) -> None:
            if not isinstance(event, RoomMessageText):
                return
            if event.sender == user_id:
                return
            room_id = room.room_id
            if allowed_rooms and room_id not in allowed_rooms:
                return
            try:
                await handle_message(
                    room_id,
                    event.sender,
                    event.body,
                    send=_send,
                )
            except Exception:
                logger.exception(
                    "matrix handle_message failed room=%s sender=%s",
                    room_id,
                    event.sender,
                )

        client.add_event_callback(_on_message, RoomMessageText)

        try:
            while True:
                try:
                    response = await client.sync(timeout=30000)
                    if isinstance(response, Exception):
                        logger.error("matrix sync error: %s", response)
                        await asyncio.sleep(3)
                except Exception as exc:
                    logger.error("matrix sync loop error: %s", exc)
                    await asyncio.sleep(3)
        finally:
            await client.close()
