import httpx
from django.conf import settings

FORUM_ICON_COLORS = [0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F]


def _base_url() -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


def redact_token(text: str) -> str:
    """Strip the bot token from any text before it reaches logs.

    httpx error messages embed the request URL, which contains the token in its
    path (.../bot<token>/...). Never log such a string un-redacted.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    return text.replace(token, "***") if token else text


async def create_forum_topic(chat_id, name, icon_color) -> int:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/createForumTopic",
            json={"chat_id": chat_id, "name": name[:128], "icon_color": icon_color},
        )
        resp.raise_for_status()
        return resp.json()["result"]["message_thread_id"]


async def edit_forum_topic(chat_id, message_thread_id, name) -> None:
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/editForumTopic",
            json={
                "chat_id": chat_id,
                "message_thread_id": message_thread_id,
                "name": name[:128],
            },
        )
        resp.raise_for_status()


async def send_message(
    chat_id,
    text,
    message_thread_id=None,
    parse_mode=None,
    reply_markup=None,
    disable_notification=None,
) -> int | None:
    payload = {"chat_id": chat_id, "text": text}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if disable_notification is not None:
        payload["disable_notification"] = disable_notification
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/sendMessage",
            json=payload,
        )
        resp.raise_for_status()
        try:
            return resp.json()["result"]["message_id"]
        except Exception:
            return None


async def edit_message_text(
    chat_id,
    message_id,
    text,
    *,
    message_thread_id=None,
    parse_mode=None,
) -> bool:
    """Edit an existing message in place. Returns True on success, False on failure.

    Editing does not re-notify recipients — that is the point of this function.
    Note: editMessageText identifies the message by (chat_id, message_id); it does
    NOT take message_thread_id (the kwarg is accepted for call-site symmetry but
    intentionally not sent, since Telegram rejects the unknown field).
    """
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/editMessageText",
            json=payload,
        )
        return resp.is_success


async def answer_callback_query(
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
) -> None:
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.post(
            f"{_base_url()}/answerCallbackQuery",
            json=payload,
        )
        resp.raise_for_status()


async def send_photo(
    chat_id,
    png: bytes,
    caption: str = "",
    message_thread_id=None,
) -> None:
    """Send a PNG image to a Telegram chat (used for QR pairing codes)."""
    payload = {"chat_id": str(chat_id)}
    if message_thread_id is not None:
        payload["message_thread_id"] = str(message_thread_id)
    if caption:
        payload["caption"] = caption
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            f"{_base_url()}/sendPhoto",
            data=payload,
            files={"photo": ("qr.png", png, "image/png")},
        )
        resp.raise_for_status()


async def get_updates(offset, timeout=50):
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=timeout + 10, write=5.0, pool=5.0)
    ) as client:
        resp = await client.get(
            f"{_base_url()}/getUpdates",
            params={"offset": offset, "timeout": timeout},
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
