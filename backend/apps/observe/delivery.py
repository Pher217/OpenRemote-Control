from channels.db import database_sync_to_async

from apps.telegram import telegram_api
from apps.telegram.telegram_api import FORUM_ICON_COLORS

TELEGRAM_MAX = 4096


def pick_color(session_id: str) -> int:
    return FORUM_ICON_COLORS[sum(ord(c) for c in session_id) % len(FORUM_ICON_COLORS)]


@database_sync_to_async
def _ensure_topic_id(thread, forum_chat_id) -> tuple[int | None, str, int]:
    existing = thread.metadata.get("telegram_topic_id")
    topic_name = f"{thread.runtime} · {thread.external_session_ref[:8]}"
    color = pick_color(thread.external_session_ref)
    return existing, topic_name, color


@database_sync_to_async
def _save_topic_id(thread, topic_id, color) -> None:
    thread.metadata["telegram_topic_id"] = topic_id
    thread.metadata["telegram_icon_color"] = color
    thread.save(update_fields=["metadata"])


async def deliver_turn(thread, parsed, msg, *, forum_chat_id, api=None) -> None:
    if api is None:
        api = telegram_api

    existing, name, color = await _ensure_topic_id(thread, forum_chat_id)
    if existing is None:
        topic_id = await api.create_forum_topic(forum_chat_id, name, color)
        await _save_topic_id(thread, topic_id, color)
    else:
        topic_id = existing

    text = f"{parsed['role']}: {parsed['text']}"
    if len(text) > TELEGRAM_MAX:
        text = text[: TELEGRAM_MAX - 1] + "…"

    await api.send_message(forum_chat_id, text, message_thread_id=topic_id)
