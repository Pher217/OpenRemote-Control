"""Minimal one-shot Matrix sender for use outside the long-poll bot loop.

Import is safe at module level; nio is imported lazily inside send_text so
the module can be imported in environments where nio is not installed.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


async def send_text(room_id: str, text: str) -> None:
    """Send a plain-text message to a Matrix room and close the client.

    Opens a fresh AsyncClient per call (no persistent connection).  The caller
    is responsible for wrapping this in async_to_sync or running it from an
    async context.  Raises on misconfiguration so the caller's best-effort
    try/except captures it.
    """
    import nio  # lazy — never required at import time

    homeserver = getattr(settings, "MATRIX_HOMESERVER", "")
    user_id = getattr(settings, "MATRIX_USER_ID", "")
    access_token = getattr(settings, "MATRIX_ACCESS_TOKEN", "")

    if not (homeserver and user_id and access_token):
        raise RuntimeError(
            "Matrix not configured: MATRIX_HOMESERVER, MATRIX_USER_ID, "
            "and MATRIX_ACCESS_TOKEN must all be set."
        )

    client = nio.AsyncClient(homeserver, user_id)
    client.access_token = access_token
    try:
        await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": text},
        )
    finally:
        await client.close()
